import httpx
from uvicorn.server import logger
from aiocache import cached, Cache

from config import *
from ..utils.helpers import get_time
from ..models import *
from ..api.alist import get_alist_raw_url

# return Alist Raw Url
@get_time
@cached(ttl=600, cache=Cache.MEMORY, key_builder=lambda f, file_path, host_url, ua, client: file_path + host_url + ua)
async def get_or_cache_alist_raw_url(file_path, host_url, ua, client: httpx.AsyncClient) -> str:
    """创建或获取Alist Raw Url缓存，缓存时间为5分钟"""    
    raw_url = await get_alist_raw_url(file_path, host_url=host_url, ua=ua, client=client)
    logger.info("Alist Raw Url: " + raw_url)
    return raw_url