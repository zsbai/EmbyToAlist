import fastapi
import httpx
from loguru import logger

from ..config import FORCE_CLIENT_RECONNECT
from ..models import RequestInfo, CacheRangeStatus
from ..cache.CacheManager import CacheManager
from ..cache.CacheSystem import CacheSystem
from typing import AsyncGenerator, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from ..utils.helpers import RawLinkManager

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        request_header: dict,
                        response_headers: dict,
                        request_info: RequestInfo,
                        status_code: int = 206
                        ):
    """
    读取缓存数据和URL，返回合并后的流

    :param cache: 缓存数据
    :param request_header: 请求头，用于请求直链，包含host和range
    :param response_headers: 返回的响应头，包含调整过的range以及content-type
    :param request_info: 请求信息
    :param status_code: HTTP响应状态码，默认为206
    
    :return: fastapi.responses.StreamingResponse
    """
    client = ClientManager.get_client()
    cache_system: CacheSystem = CacheManager.get_cache_system()
    
    cache_start, cache_end = request_info.range_info.cache_range
    cache_size = cache_end - cache_start + 1
    
    async def merged_stream() -> AsyncGenerator[bytes, None]:
        writer = None
        written = 0
        total_bytes_downloaded = 0
        
        try:
            # 如果缓存存在，先 yield 出缓存数据
            if cache is not None:
                async for chunk in cache:
                    yield chunk
                logger.info("Cache exhausted, streaming from source")
            else:
                # 当缓存不存在且允许写缓存时，获取缓存写入器
                if request_info.cache_range_status != CacheRangeStatus.NOT_CACHED:
                    writer = cache_system.get_writer(request_info)
            
            raw_link_manager = request_info.raw_link_manager
            raw_url = await raw_link_manager.get_raw_url()
            
            request_header['host'] = raw_url.split('/')[2]
            logger.debug(f"Requesting {raw_url} with headers {request_header}")
            
            if writer is not None:
                async with writer:
                    async with client.stream("GET", raw_url, headers=request_header) as response:
                        verify_download_response(response)
                        if status_code == 206 and response.status_code != 206:
                            raise ValueError(f"Expected 206 response, got {response.status_code}")
                        
                        async for chunk in response.aiter_bytes():
                            total_bytes_downloaded += len(chunk)
                            if written < cache_size:          
                                remaining = cache_size - written
                                if remaining <= 0:
                                    break
                                chunk_size = min(remaining, len(chunk))
                                await writer.write(chunk[:chunk_size])
                                written += chunk_size
                            
                            # 从后端传输大于1MB的数据后，强制断开连接
                            if FORCE_CLIENT_RECONNECT and total_bytes_downloaded > cache_size + 1024*1024:
                                raise ForcedReconnectError()
                            yield chunk
            else:
                async with client.stream("GET", raw_url, headers=request_header) as response:
                    verify_download_response(response)
                    if status_code == 206 and response.status_code != 206:
                        raise ValueError(f"Expected 206 response, got {response.status_code}")
                    
                    async for chunk in response.aiter_bytes():
                        total_bytes_downloaded += len(chunk)
                        # 从后端传输大于1MB的数据后，强制断开连接
                        if FORCE_CLIENT_RECONNECT:
                            if cache is not None and total_bytes_downloaded > 1024*1024:
                                raise ForcedReconnectError()
                            
                        yield chunk
                    
        except ForcedReconnectError as e:
            logger.info(f"Expected ForcedReconnectError: {e}")
            raise fastapi.HTTPException(status_code=500, detail="Force Reconnect")
        except Exception as e:
            logger.error(f"Reverse_proxy failed, {e}")
            raise fastapi.HTTPException(status_code=500, detail="Reverse Proxy Failed")
        finally:
            # 检查写入的数据是否与缓存大小相等
            if writer is not None and written != cache_size:
                logger.error(f"Cache Write Error: Written {written} bytes, expected {cache_size} bytes")
            logger.debug(f"Total Bytes Downloaded: {total_bytes_downloaded}")
            if writer is not None:
                logger.debug(f"Total Bytes Written: {written}")
            else:
                logger.debug("No Cache Write")

    return fastapi.responses.StreamingResponse(
        merged_stream(), 
        headers=response_headers, 
        status_code=status_code
        )

async def temporary_redirect(raw_link_manager: 'RawLinkManager') -> fastapi.Response:
    """重定向到alist直链
    
    :param raw_link_manager: RawLinkManager实例
    
    :return fastapi.Response: 重定向到alist直链的响应
    """
    raw_url = await raw_link_manager.get_raw_url()
    return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)

def verify_download_response(resposne: httpx.Response):
    """验证status_code, 验证响应header

    Args:
        resposne (httpx.Response): HTTPX响应对象
    """
    if resposne.status_code == 416:
        logger.warning("Reponse Verification: 416 Range Not Satisfiable")
        logger.debug(f"Valid Range: {resposne.headers.get('Content-Range')}")
        raise ValueError("Reponse Verification Failed: Range Not Satisfiable")
    if resposne.status_code == 400:
        logger.warning("Reponse Verification: 400 Bad Request")
        logger.debug(f"Response Text: {resposne.text}")
        logger.debug(f"Response Headers: {resposne.headers}")
        raise ValueError("Reponse Verification Failed: 400 Bad Request")
    
    resposne.raise_for_status()
    
    content_type = resposne.headers.get('Content-Type')
    if "application/json;" in content_type:
        logger.warning("Reponse Verification: JSON Response")
        logger.debug(f"Response Text: {resposne.text}")
        raise ValueError("Reponse Verification Failed: JSON Response")
    

class ForcedReconnectError(Exception):
    """预期异常，用于强制播放器重新请求"""
    def __init__(self, message="Expected Error, Force Break the Connection"):
        self.message = message
        super().__init__(message)

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
            cls._client.aclose()