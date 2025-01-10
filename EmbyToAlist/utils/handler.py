import fastapi
import httpx
from uvicorn.server import logger

from ..config import CACHE_NEXT_EPISODE
from .network import reverse_proxy
from ..cache.media import cache_next_episode
from ..models import RequestInfo, CacheStatus
from typing import AsyncGenerator

# 在第一个请求到达时就异步创建alist缓存任务
# 重定向的情况：
# 1. 未启用缓存
# 2. 请求头不包含Range
# 3. 中间恢复播放
# 反代的情况：
# 1. 无缓存文件（should，目前只是重新代理。todo：缓存重利用）
# 2. 缓存拼接
# 只需返回缓存（不需要alist直链）的情况：
# 1. 请求范围在缓存范围内
# 2. 请求范围在文件末尾2MB内
async def request_handler(expected_status_code: int,
                          cache: AsyncGenerator[bytes, None]=None,
                          request_info: RequestInfo=None,
                          resp_header: dict=None,
                          background_tasks: fastapi.BackgroundTasks=None,
                          client: httpx.AsyncClient=None
                          ) -> fastapi.Response:
    """决定反代还是重定向，创建alist缓存
    
    :param expected_status_code: 期望返回的状态码，302或206
    :param cache: 内部缓存数据
    :param request_info: 请求信息
    :param resp_header: 需要返回的响应头
    :param client: httpx异步请求客户端
    
    :return fastapi.Response: 返回重定向或反代的响应
    """
    
    if request_info.cache_status != CacheStatus.UNKNOWN and background_tasks is not None and CACHE_NEXT_EPISODE is True:
        background_tasks.add_task(cache_next_episode, request_info=request_info, api_key=request_info.api_key, client=client)
        logger.info("Started background task to cache next episode.")
        
    alist_raw_url_task = request_info.raw_url_task

    if expected_status_code == 416:
        return fastapi.responses.Response(status_code=416, headers=resp_header)
    
    if expected_status_code == 302:
        raw_url = await alist_raw_url_task
        return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)
    
    request_header = dict(request_info.headers)
    
    if expected_status_code == 206:
        start_byte = request_info.start_byte
        end_byte = request_info.end_byte
        local_cache_size = request_info.file_info.cache_file_size
        cache_status = request_info.cache_status

        if cache_status == CacheStatus.MISS:
            # Case 1: Requested range is entirely beyond the cache
            # Prepare Range header
            if end_byte is not None:
                source_range_header = f"bytes={start_byte}-{end_byte - 1}"
            else:
                source_range_header = f"bytes={start_byte}-"

            request_header["Range"] = source_range_header
            return await reverse_proxy(
                cache=None, 
                url_task=alist_raw_url_task, 
                request_header=request_header,
                response_headers=resp_header,
                client=client
                )
        elif cache_status in {CacheStatus.HIT, CacheStatus.HIT_TAIL}:
            # Case 2: Requested range is entirely within the cache
            return fastapi.responses.StreamingResponse(cache, headers=resp_header, status_code=206)
        else:
            # Case 3: Requested range overlaps cache and extends beyond it
            source_start = local_cache_size
            
            if end_byte is not None:
                source_range_header = f"bytes={source_start}-{end_byte}"
            else:
                source_range_header = f"bytes={source_start}-"
            
            request_header["Range"] = source_range_header
            return await reverse_proxy(
                cache=cache, 
                url_task=alist_raw_url_task, 
                request_header=request_header,
                response_headers=resp_header,
                client=client
                )
        
    if expected_status_code == 200:
        request_header["Range"] = f"bytes={request_info.file_info.cache_file_size}-"
        return await reverse_proxy(
            cache=cache,
            url_task=alist_raw_url_task,
            request_header=request_header,
            response_headers=resp_header,
            client=client,
            status_code=200
            )
    
    raise fastapi.HTTPException(status_code=500, detail=f"Unexpected argument: {expected_status_code}")
