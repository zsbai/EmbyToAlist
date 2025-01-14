import fastapi
import httpx
from uvicorn.server import logger
from aiolimiter import AsyncLimiter

from typing import AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from .handler import RawLinkManager

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        raw_link_manager: 'RawLinkManager',
                        request_header: dict,
                        response_headers: dict,
                        client: httpx.AsyncClient,
                        status_code: int = 206
                        ):
    """
    读取缓存数据和URL，返回合并后的流

    :param cache: 缓存数据
    :param url_task: 源文件的URL的异步任务
    :param request_header: 请求头，用于请求直链，包含host和range
    :param response_headers: 返回的响应头，包含调整过的range以及content-type
    :param client: HTTPX异步客户端
    :param status_code: HTTP响应状态码，默认为206
    
    :return: fastapi.responses.StreamingResponse
    """
    limiter = AsyncLimiter(10*1024*1024, 1)
    async def merged_stream() -> AsyncGenerator[bytes, None]:
        try:
            if cache is not None:
                async for chunk in cache:
                    await limiter.acquire(len(chunk))
                    yield chunk
                logger.info("Cache exhausted, streaming from source")
            
            raw_url = await raw_link_manager.get_raw_url()
            
            request_header['host'] = raw_url.split('/')[2]
            logger.debug(f"Requesting {raw_url} with headers {request_header}")
            async with client.stream("GET", raw_url, headers=request_header) as response:
                verify_download_response(response)
                if status_code == 206 and response.status_code != 206:
                    raise ValueError(f"Expected 206 response, got {response.status_code}")
                async for chunk in response.aiter_bytes():
                    await limiter.acquire(len(chunk))
                    yield chunk
        except Exception as e:
            logger.error(f"Reverse_proxy failed, {e}")
            raise fastapi.HTTPException(status_code=500, detail="Reverse Proxy Failed")

    return fastapi.responses.StreamingResponse(
        merged_stream(), 
        headers=response_headers, 
        status_code=status_code
        )
    
async def verify_download_response(resposne: httpx.Response):
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
    