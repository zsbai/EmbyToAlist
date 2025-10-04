import asyncio
import re
from functools import wraps, partial

import httpx
from loguru import logger
import fastapi

from ..providers.media_server.emby.client import EmbyClient
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    pass

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


def async_wrap(func):
    """Transform a synchronous function to an asynchronous one."""

    @wraps(func)
    async def run(*args, loop=None, executor=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        pfunc = partial(func, *args, **kwargs)
        return await loop.run_in_executor(executor, pfunc)

    return run

class ClientManager():
    _client: Optional[httpx.AsyncClient] = None

    @classmethod
    def init_client(cls):
        if cls._client is None:
            cls._client = httpx.AsyncClient()

    @classmethod
    def get_client(cls):
        if cls._client is None:
            logger.error("Request Client not initialized")
            raise ValueError("Request Client not initialized")
        return cls._client

    @classmethod
    async def close_client(cls):
        if cls._client is not None:
            await cls._client.aclose()

    @classmethod
    def get_emby_client(cls, api_key: str) -> 'EmbyClient':
        """Return a per-request EmbyClient bound to api_key, sharing the httpx client."""
        if cls._client is None:
            cls.init_client()
        return EmbyClient(api_key=api_key, client=cls._client)


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
    """从Emby的请求中提取API密钥"""
    api_key = request.query_params.get('api_key') or request.query_params.get('X-Emby-Token')
    if not api_key:
        # For Infuse
        auth_header = request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
        else:
            # Sometimes Fileball uses x-emby-token header
            auth_header = request.headers.get('x-emby-token')
            if auth_header:
                api_key = auth_header
    return api_key
