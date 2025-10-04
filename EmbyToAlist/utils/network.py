import asyncio
from typing import AsyncGenerator, Optional, TYPE_CHECKING

import fastapi
import httpx
from loguru import logger

from ..models import RequestInfo, CacheRangeStatus, FileHeaders
from ..cache.manager import AppContext
from ..cache.system import CacheSystem
from ..utils.common import ClientManager
if TYPE_CHECKING:
    from ..service.manager import RawLinkManager

async def stream_handler(
    cache: Optional[AsyncGenerator[bytes, None]],
    response_headers: dict,
    request_info: RequestInfo,
    status_code: int = 206
    ) -> fastapi.responses.StreamingResponse:
    """
    将缓存数据和后端数据合并成一个流返回给客户端。

    :param cache: 缓存数据
    :param response_headers: 返回的响应头，包含调整过的range以及content-type
    :param request_info: 请求信息
    :param status_code: HTTP响应状态码，默认为206
    
    :return: fastapi.responses.StreamingResponse
    """
    cache_system: CacheSystem = AppContext.get_cache_system()
    
    # 尝试从缓存中获取文件头信息并添加到响应头中
    try:
        file_headers = await cache_system.storage.get_file_headers(request_info.file_info)
        if file_headers:
            if file_headers.etag:
                response_headers['ETag'] = file_headers.etag
            if file_headers.last_modified:
                response_headers['Last-Modified'] = file_headers.last_modified
            if file_headers.content_disposition and 'Content-Disposition' not in response_headers:
                response_headers['Content-Disposition'] = file_headers.content_disposition
            logger.debug(f"Added cached headers to response: ETag={file_headers.etag}, Last-Modified={file_headers.last_modified}")
    except Exception as e:
        logger.warning(f"Failed to retrieve cached headers: {e}")
    
    async def merged_stream() -> AsyncGenerator[bytes, None]:

        try:
            cache_stream: Optional[AsyncGenerator[bytes, None]] = cache
            # 如果缓存不存在，但是需要缓存，则启动缓存写入
            if cache_stream is None and request_info.cache_range_status != CacheRangeStatus.NOT_CACHED:
                logger.debug("Cache is None, start writing cache")
                await cache_system.start_write_cache_file(request_info)
                cache_stream = await cache_system.get_cache_file(request_info)

            response_range = request_info.range_info.response_range
            if response_range is None:
                raise fastapi.HTTPException(status_code=500, detail="Response range is not set")

            response_start, response_end = response_range
            expected_total = response_end - response_start + 1
            bytes_sent = 0

            if cache_stream is not None:
                logger.debug("Streaming from cache")
                async for chunk in cache_stream:
                    if not chunk:
                        continue
                    yield chunk
                    bytes_sent += len(chunk)
                logger.debug(f"Cache streaming finished, bytes sent: {bytes_sent}")

            remaining_total = expected_total - bytes_sent

            # 此时判断缓存内容是否已经满足请求段
            if remaining_total <= 0:
                return

            if request_info.is_HIGH_COMPAT_MEDIA_CLIENTS:
                logger.debug("High compatibility client finished cache segment, ending stream")
                if remaining_total > 0:
                    logger.warning("High compatibility client expected to finish within cache, but remaining data detected")
                return

            reverse_start = response_start + bytes_sent
            reverse_end = response_end
            logger.debug(f"Falling back to reverse proxy from {reverse_start} to {reverse_end}")

            async for chunk in reverse_proxy(request_info, reverse_start, reverse_end):
                if not chunk:
                    continue
                remaining_total -= len(chunk)
                if remaining_total < 0:
                    # 截断多余数据，避免超出声明长度
                    yield chunk[:remaining_total + len(chunk)]
                    logger.warning("Reverse proxy provided more data than expected, truncating output")
                    break
                yield chunk
                if remaining_total == 0:
                    break

        except asyncio.CancelledError:
            logger.warning("Streaming cancelled by client")
            raise
        except Exception as e:
            logger.error(f"Merged stream failed: {e}")
            raise fastapi.HTTPException(status_code=502, detail="Reverse Proxy Failed")

    logger.debug(f"Response Headers: {response_headers}")
    return fastapi.responses.StreamingResponse(
        merged_stream(), 
        headers=response_headers, 
        status_code=status_code
        )

async def reverse_proxy(
    request_info: RequestInfo,
    start: int,
    end: int
) -> AsyncGenerator[bytes, None]:
    """通过直链反向代理剩余数据段"""

    if start > end:
        logger.debug("Reverse proxy start is greater than end, skipping")
        return

    if request_info.raw_link_manager is None:
        raise fastapi.HTTPException(status_code=500, detail="Raw link manager is not initialized")

    raw_url = await request_info.raw_link_manager.get_raw_url()
    headers = {
        "Range": f"bytes={start}-{end}",
        "User-Agent": request_info.raw_link_manager.ua,
    }

    try:
        parsed_url = httpx.URL(raw_url)
        if parsed_url.host:
            headers["Host"] = parsed_url.host
    except Exception as e:
        logger.warning(f"Failed to parse raw url host: {e}")

    client = ClientManager.get_client()

    try:
        async with client.stream("GET", raw_url, headers=headers) as response:
            if response.status_code not in {200, 206}:
                logger.error(f"Reverse proxy unexpected status: {response.status_code}")
                response.raise_for_status()

            async for chunk in response.aiter_bytes():
                yield chunk

    except asyncio.CancelledError:
        logger.warning("Reverse proxy stream cancelled by client")
        raise
    except Exception as e:
        logger.error(f"Reverse proxy request failed: {e}")
        raise fastapi.HTTPException(status_code=502, detail="Reverse Proxy Failed")


async def temporary_redirect(raw_link_manager: 'RawLinkManager') -> fastapi.Response:
    """重定向到alist直链
    
    :param raw_link_manager: RawLinkManager实例
    
    :return fastapi.Response: 重定向到alist直链的响应
    """
    raw_url = await raw_link_manager.get_raw_url()
    return fastapi.responses.RedirectResponse(url=raw_url, status_code=307)
