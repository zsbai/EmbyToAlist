import fastapi
import httpx
from uvicorn.server import logger
from aiolimiter import AsyncLimiter

from typing import AsyncGenerator

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        url_task: str,
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
    async def merged_stream():
        try:
            if cache is not None:
                async for chunk in cache:
                    await limiter.acquire(len(chunk))
                    yield chunk
                logger.info("Cache exhausted, streaming from source")
            
            raw_url = await url_task
            
            request_header['host'] = raw_url.split('/')[2]
            logger.debug(f"Requesting {raw_url} with headers {request_header}")
            async with client.stream("GET", raw_url, headers=request_header) as response:
                response.raise_for_status()
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
