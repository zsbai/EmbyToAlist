import re
import asyncio

import fastapi
import httpx
from loguru import logger
from aiocache import cached, Cache

from ..api.alist import get_alist_raw_url
from ..models import RequestInfo

# a wrapper function to get the time of the function
def get_time(func):
    def wrapper(*args, **kwargs):
        import time
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"Function {func.__name__} takes: {end - start} seconds")
        return result
    return wrapper

def get_content_type(container) -> str:
    """文件格式对应的Content-Type映射"""
    content_types = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska',
        'ts': 'video/mp2t',
    }

    # 返回对应的Content-Type，如果未找到，返回一个默认值
    return content_types.get(container.lower(), 'application/octet-stream')

def extract_api_key(request: fastapi.Request):
    """从请求中提取API密钥"""
    api_key = request.query_params.get('api_key') or request.query_params.get('X-Emby-Token')
    if not api_key:
        auth_header = request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
    return api_key

def validate_regex(word: str) -> bool:
    """
    验证用户输入是否为有效的正则表达式
    """
    try:
        re.compile(word)
        return True
    except re.error:
        return False

def match_with_regex(pattern, target_string):
    """
    使用正则表达式匹配目标字符串
    """
    if validate_regex(pattern):
        match = re.search(pattern, target_string)
        if match:
            return True
        else:
            return False
    else:
        raise ValueError("Invalid regex pattern")
    
class RawLinkManager():
    """管理alist直链获取任务
    支持普通文件和strm文件
    """
    cache = Cache(Cache.MEMORY)
    
    def __init__(self, 
                 path: str,
                 request_info: RequestInfo,
                 client: httpx.AsyncClient):
        self.path = path
        self.is_strm = request_info.file_info.is_strm
        self.ua = request_info.headers.get("user-agent")
        self.client = client
        self.raw_url = None
        self.task = None
        
    async def create_task(self) -> None:
        # 如果任务已存在:
        if self.task and not self.task.done():
            return
        
        self.raw_url = await self.cache.get(f"raw_url:{self.path}:{self.ua}", None)
        
        # 如果已经获取到直链:
        if self.raw_url is not None:
            return

        self.task = asyncio.create_task(self.request_raw_url())
            
        self.task.add_done_callback(self.on_task_done)
        return
    
    async def request_raw_url(self) -> str:
        if self.is_strm:
            raw_url = await self.precheck_strm()
        else:
            raw_url = await get_alist_raw_url(
                self.path,
                self.ua,
                self.client
                )
        await self.cache.set(f"raw_url:{self.path}:{self.ua}", raw_url, ttl=600)
        return raw_url
        
    
    async def precheck_strm(self) -> str:
        """预先请求strm文件地址，以便在请求时直接返回直链

        Returns:
            str: strm文件中的直链
        """
        async with self.client.stream("GET", self.path, headers={
            "user-agent": self.ua
            }) as response:
            if response.status_code in {302, 301}:
                location = response.headers.get("Location")
                if location: 
                    logger.debug(f"Strm file redirected to {location}")
                    return location
                raise fastapi.HTTPException(status_code=500, detail="No Location header in response")
            elif response.status_code == 200:
                # path中存储的是直链
                return self.path
            else:
                response.raise_for_status()
            
            raise fastapi.HTTPException(status_code=500, detail="Failed to request strm file")
    
    async def get_raw_url(self) -> str:
        if self.raw_url is not None:
            return self.raw_url
          
        if await self.cache.exists(f"raw_url:{self.path}:{self.ua}"):
            self.raw_url = await self.cache.get(f"raw_url:{self.path}:{self.ua}")
            logger.debug(f"Cache hit for {self.path}")
            return self.raw_url
          
        if self.task is None:
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task not created")
        try:
            return await self.task
        except asyncio.CancelledError:
            logger.warning("RawLinkManager task was cancelled")
        except Exception as e:
            logger.error(f"Error: RawLinkManager task failed for path {self.path}, error: {e}")
            raise fastapi.HTTPException(status_code=500, detail="RawLinkManager task")
        
    def on_task_done(self, task) -> None:
        self.raw_url = task.result()
    
    def cancel_task(self) -> None:
        self.task.cancel()
        return