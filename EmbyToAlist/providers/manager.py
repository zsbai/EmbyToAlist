import asyncio
import hashlib
import json

import fastapi
from aiocache import Cache
import httpx
from loguru import logger
from tenacity import RetryError, retry, retry_if_exception_type, stop_after_attempt, wait_fixed, before_log, after_log

from .rawlink.alist.rawlink import get_alist_raw_url
from .media_server.emby.rawlink import get_emby_raw_url
from ..cache.manager import AppContext
from ..config import ENABLE_UA_PASSTHROUGH, RAW_LINK_PROVIDER
from ..utils.common import ClientManager
from typing import Optional

class RawLinkManager():
    """管理alist直链获取任务和缓存

    支持普通文件和strm文件
    """
    cache = Cache(Cache.MEMORY)

    def __init__(
        self,
        path: str,
        is_strm: bool,
        ua: str = None,
        enable_ua_passthrough: bool = ENABLE_UA_PASSTHROUGH
    ):
        if enable_ua_passthrough and ua is None:
            raise fastapi.HTTPException(status_code=500, detail="User-Agent passthrough is enabled, but User-Agent is None")

        if not enable_ua_passthrough:
            # use default media player user-agent
            self.ua = "mpv/0.33.1"
        else:
            self.ua = ua

        self.path = path.strip()
        self.is_strm = is_strm
        self.client = ClientManager.get_client()
        self.raw_url = None

        self.task_manager = AppContext.get_task_manager()

        ua_hash = hashlib.md5(self.ua.encode()).hexdigest()
        # 使用md5哈希值作为缓存key的一部分，避免过长的key
        self.key = f"raw_url:{self.path}:{ua_hash}"
        self.task_sub_key = self.ua  # 以UA区分任务

    async def create_task(self) -> None:
        # 如果缓存中没有，则使用 TaskManager 创建唯一任务
        if await self.cache.exists(self.key):
            self.raw_url = await self.cache.get(self.key)
            logger.debug(f"Raw Url Cache hit for {self.path}(url: {self.raw_url}), skip creating task")
            return

        existing_task = await self.task_manager.get_task(RawLinkTask, self.path, sub_key=self.task_sub_key)
        if existing_task:
            logger.debug(f"Task already exists for {self.path} - reuse")
            return

        task = RawLinkTask(self.path, self.is_strm, self.ua)
        task.run()
        
        await self.task_manager.create_task(RawLinkTask, self.path, task, sub_key=self.task_sub_key, ttl=600)

    async def get_raw_url(self) -> str:
        """用于外部获取直链（自动触发任务/复用任务）"""
        if self.raw_url is not None:
            return self.raw_url

        if await self.cache.exists(self.key):
            self.raw_url = await self.cache.get(self.key)
            logger.debug(f"Cache hit for {self.path}(url: {self.raw_url}), return cached raw_url")
            return self.raw_url

        task: Optional[RawLinkTask] = await self.task_manager.get_task(RawLinkTask, self.path, sub_key=self.task_sub_key)
        if not task:
            raise fastapi.HTTPException(status_code=500, detail="RawLinkTask not created")

        try:
            self.raw_url = await task.get_result()
            await self.cache.set(self.key, self.raw_url, ttl=3600)
            return self.raw_url
        except RetryError as e:
            logger.error(f"Error: RawLinkTask failed for path {self.path}, error: {e}")
            await self.task_manager.remove_task(RawLinkTask, self.path, sub_key=self.task_sub_key)
            raise fastapi.HTTPException(status_code=500, detail="RawLinkTask failed after retries")
        except Exception as e:
            logger.error(f"Error: RawLinkTask failed for path {self.path}, error: {e}")
            await self.task_manager.remove_task(RawLinkTask, self.path, sub_key=self.task_sub_key)
            raise fastapi.HTTPException(status_code=500, detail="RawLinkTask failed")

class RawLinkTask():
    """
    获取直链的异步任务
    
    """

    def __init__(
        self,
        path: str,
        is_strm: bool,
        ua: str = None,
        provider: str = RAW_LINK_PROVIDER,
    ):
        self.path = path
        self.is_strm = is_strm
        self.ua = ua
        self.provider = (provider or 'alist').lower()
        
        self.client = ClientManager.get_client()
        
        self.task: Optional[asyncio.Task] = None
        self.raw_url = None
    
    def run(self) -> asyncio.Task:
        self.task = asyncio.create_task(self._run())
        return self.task

    async def _run(self) -> str:
        logger.debug(f"[{self.provider}] Fetching raw link for {self.path} (is_strm={self.is_strm})")
        if self.raw_url is not None:
            return self.raw_url
        
        if self.is_strm:
            if not self.path.startswith(("http://", "https://")):
                raise fastapi.HTTPException(status_code=500, detail="STRM file path is not a valid URL")
            self.raw_url = await self.precheck_strm()
        else:
            if self.provider == 'emby':
                self.raw_url = await get_emby_raw_url(self.path)
            elif self.provider == 'alist':
                self.raw_url = await get_alist_raw_url(self.path, self.ua)
            else:
                raise fastapi.HTTPException(status_code=500, detail=f"Unsupported provider: {self.provider}")
        return self.raw_url
    
    async def get_result(self) -> str:
        if self.raw_url is not None:
            return self.raw_url
        else:
            if self.task is None:
                raise fastapi.HTTPException(status_code=500, detail="RawLinkTask not started")
            # 避免任务被取消
            return await asyncio.shield(self.task)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(0.1),
        reraise=True,
        before_sleep=lambda retry_state: logger.info(f"[{retry_state.attempt_number}/5] Request Strm Raw Url failed ({retry_state.outcome.exception()}), retrying..."),
        retry=retry_if_exception_type((httpx.ReadTimeout, httpx.RequestError))
    )
    async def precheck_strm(self) -> str:
        """预先请求strm文件地址，以便在请求时直接返回直链

        Returns:
            str: strm文件中的直链
        """
        logger.info(f"Checking strm link: {repr(self.path)}")
        # 流式请求可以避免获取响应体
        async with self.client.stream(
            "GET",
            self.path, 
            headers={
                "user-agent": self.ua
            },
            timeout=10
        ) as response:
            if response.status_code in {302, 301, 307, 308}:
                location = response.headers.get("Location")
                if location:
                    logger.debug(f"Strm file redirected to {location}")
                    return location
                raise fastapi.HTTPException(status_code=500, detail="No Location header in response")
            elif response.status_code == 200:
                # 避免响应错误信息
                if "application/json" in response.headers.get("Content-Type", "").lower():
                    body = await response.aread()
                    body = json.loads(body.decode())
                    logger.warning(f"Strm file returned JSON: {body}")
                    raise fastapi.HTTPException(status_code=500, detail="Strm file returned JSON response")

                # path中存储的是直链
                return self.path
            else:
                response.raise_for_status()

            raise fastapi.HTTPException(status_code=500, detail="Failed to request strm file")