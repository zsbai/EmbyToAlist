import httpx
from uvicorn.server import logger
from aiocache import cached, Cache

from ..utils.helpers import get_time
from ..api.alist import get_alist_raw_url
from ..models import RequestInfo

# return Alist Raw Url
@get_time
@cached(ttl=600, cache=Cache.MEMORY, key_builder=lambda f, request_info, client: request_info.file_info.path+request_info.headers.get('User-Agent'))
async def get_or_cache_alist_raw_url(request_info: RequestInfo, client: httpx.AsyncClient) -> str:
    """创建或获取Alist Raw Url缓存，缓存时间为5分钟"""    
    raw_url = await get_alist_raw_url(request_info.file_info.path, ua=request_info.headers.get("User-Agent"), client=client)
    logger.info("Alist Raw Url: " + raw_url)
    return raw_url