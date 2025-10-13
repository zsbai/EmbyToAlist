import asyncio
import hashlib
import json

import fastapi
from aiocache import Cache
from loguru import logger

from .rawlink.alist.rawlink import get_alist_raw_url
from .media_server.emby.rawlink import get_emby_raw_url
from ..cache.manager import AppContext
from ..config import ENABLE_UA_PASSTHROUGH, RAW_LINK_PROVIDER
from ..utils.common import ClientManager

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
            logger.debug(f"Raw Url Cache hit for {self.path}")
            return

        existing_task = await self.task_manager.get_task(RawLinkManager, self.path, sub_key=self.task_sub_key)
        if existing_task:
            logger.debug(f"Task already exists for {self.path} - reuse")
            return

        task = asyncio.create_task(self._wrapped_download())
        await self.task_manager.create_task(RawLinkManager, self.path, task, sub_key=self.task_sub_key, ttl=600)

    async def _wrapped_download(self):
        """
        包装获取直链的协程函数，加入协程池
        """
        try:
            raw_url = await self.cache_raw_url()
            await self.cache.set(self.key, raw_url, ttl=3600)
            self.raw_url = raw_url
            return raw_url
        except Exception as e:
            logger.error(f"Error: Failed to get raw url for path {self.path}, error: {repr(e)}")
            raise


    async def cache_raw_url(self) -> str:
        """获取alist直链并缓存
        1. 如果是strm文件，请求strm，缓存真正的文件链接
        2. 如果是普通文件，使用 provider 获取直链（alist 或 emby）
        """
        provider = (RAW_LINK_PROVIDER or 'alist').lower()
        if provider == 'emby':
            # Emby 模式：忽略 is_strm，直接通过第二台 Emby 解析直链
            return await get_emby_raw_url(self.path)
        # 默认/Alist 模式
        if self.is_strm:
            return await self.precheck_strm()
        return await get_alist_raw_url(self.path, self.ua)

    async def precheck_strm(self) -> str:
        """预先请求strm文件地址，以便在请求时直接返回直链

        Returns:
            str: strm文件中的直链
        """
        logger.info(f"Checking strm link: {repr(self.path)}")
        # 流式请求可以避免获取响应体
        async with self.client.stream("GET", self.path, headers={
            "user-agent": self.ua
            }) as response:
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

    async def get_raw_url(self) -> str:
        """用于外部获取直链（自动触发任务/复用任务）"""
        if self.raw_url is not None:
            return self.raw_url

        if await self.cache.exists(self.key):
            self.raw_url = await self.cache.get(self.key)
            logger.debug(f"Cache hit for {self.path}")
            return self.raw_url

        task = await self.task_manager.get_task(RawLinkManager, self.path, sub_key=self.task_sub_key)
        if not task:
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task not created")

        try:
            return await task
        except asyncio.CancelledError:
            logger.warning("RawLinkManager task was cancelled")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task was cancelled")
        except Exception as e:
            logger.error(f"Error: RawLinkManager task failed for path {self.path}, error: {e}")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task")
