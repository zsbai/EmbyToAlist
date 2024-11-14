from contextlib import asynccontextmanager
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

import fastapi
import httpx
import uvicorn
from uvicorn.server import logger

from config import *
from components.utils import *
from components.cache import *
from typing import Tuple, Optional

# 使用上下文管理器，创建异步请求客户端
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    app.requests_client = httpx.AsyncClient()
    yield
    await app.requests_client.aclose()

app = fastapi.FastAPI(lifespan=lifespan)

URL_CACHE = {}

class CacheStatus(Enum):
    """ 本地缓存状态 """
    
    HIT = "Hit"
    """ 缓存完全命中 """
    MISS = "Miss"
    """ 缓存未命中 """
    PARTIAL = "Partial"
    """ 缓存部分命中，响应内容拓展至缓存外 """
    HIT_TAIL = "Hit_Tail"
    """ 缓存完全命中，且请求在文件末尾2MB内 """
    UNKNOWN = "Unknown"
    """ 未知状态 """

@dataclass
class FileInfo:
    # status: bool
    path: str
    bitrate: int
    size: int
    container: str
    type: str
    cache_file_size: int
    
@dataclass
class RequestInfo:
    file_info: FileInfo
    host_url: str
    start_byte: Optional[int] = None
    end_byte: Optional[int] = None
    cache_status: CacheStatus = CacheStatus.UNKNOWN

# used to get the file info from emby server
async def get_file_info(item_id, media_source_id, api_key, client: httpx.AsyncClient) -> FileInfo:
    """
    从Emby服务器获取文件信息
    
    :param item_id: Emby Item ID
    :param MediaSourceId: Emby MediaSource ID
    :param apiKey: Emby API Key
    :return: 包含文件信息的字典
    """
    media_info_api = f"{emby_server}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    item_info_api = f"{emby_server}/emby/Items?api_key={api_key}&Ids={item_id}"
    logger.info(f"Requested Info URL: {media_info_api}")
    try:
        media_info = await client.get(media_info_api)
        item_info = await client.get(item_info_api)
        media_info.raise_for_status()
        media_info = media_info.json()
        item_info.raise_for_status()
        item_info = item_info.json()
    except Exception as e:
        logger.error(f"Error: failed to request Emby server, {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")
    
    item_type = item_info['Items'][0]['Type'].lower()
    if item_type != 'movie': item_type = 'episode'
    for i in media_info['MediaSources']:
        if i['Id'] == media_source_id:
            return FileInfo(
                path=i.get('Path', None),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                type=item_type,
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            )
    # can't find the matched MediaSourceId in MediaSources
    raise fastapi.HTTPException(status_code=500, detail="Can't match MediaSourceId")
    
# return Alist Raw Url
@get_time
async def get_or_cache_alist_raw_url(file_path, host_url, client=httpx.AsyncClient) -> Tuple[int, str]:
    """创建或获取Alist Raw Url缓存，缓存时间为5分钟"""
    cache_key = file_path + host_url
    if cache_key in URL_CACHE.keys():
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[cache_key]['time'] < 300:
            logger.debug("Alist Raw URL Cache exists and is valid (less than 5 minutes)")
            logger.info("Redirected Url: " + URL_CACHE[cache_key]['url'])
            return 200, URL_CACHE[cache_key]['url']
        else:
            logger.debug("Alist Raw URL Cache is expired, re-fetching...")
            del URL_CACHE[cache_key]
    
    code, raw_url = await get_alist_raw_url(file_path, host_url=host_url, client=client)
    
    if code == 200:
        URL_CACHE[cache_key] = {
            'url': raw_url,
            'time': datetime.now().timestamp()
            }
        # print("Redirected Url: " + raw_url)
        logger.info("Redirected Url: " + raw_url)
        return code, raw_url
    else:
        # print(f"Error: failed to get Alist Raw Url, {code}")
        # print(f"{raw_url}")
        logger.error(f"Error: failed to get Alist Raw Url, Status Code: {code}")
        logger.error(f"Detailed Error: {raw_url}")
        return code, raw_url

# 可以在第一个请求到达时就异步创建alist缓存
# 重定向：
# 1. 未启用缓存
# 2. 请求头不包含Range
# 3. 中间恢复播放
# 反代：
# 1. 无缓存文件（should，目前只是重新代理。todo：缓存重利用）
# 2. 缓存拼接
# 只需返回缓存（不需要alist直链）：
# 1. 请求范围在缓存范围内
# 2. 请求范围在文件末尾2MB内
async def request_handler(expected_status_code: int,
                          cache: AsyncGenerator[bytes, None]=None,
                          request_info: RequestInfo=None,
                          resp_header: dict=None,
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
    host_url = request_info.host_url
    file_path = request_info.file_info.path
    # 如果满足alist直链条件，提前通过异步缓存alist直链
    alist_raw_url = asyncio.create_task(get_or_cache_alist_raw_url(file_path=file_path, host_url=host_url, client=client))
    
    if expected_status_code == 302:
        code, raw_url = await alist_raw_url
        if code != 200:
            raise fastapi.HTTPException(status_code=500, detail=f"Get Alist Raw Url Error: {raw_url};\nCode: {code}")
        return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)
    
    if expected_status_code == 206:
        # start_byte, end_byte, local_cache_size = range_header
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

            return await reverse_proxy(cache=None, 
                                       url_task=alist_raw_url, 
                                       request_header={
                                           "Range": source_range_header
                                           },
                                       response_headers=resp_header,
                                       client=client)
        elif cache_status == CacheStatus.HIT or cache_status == CacheStatus.HIT_TAIL:
            # Case 2: Requested range is entirely within the cache
            return fastapi.responses.StreamingResponse(cache, headers=resp_header, status_code=206)
        else:
            # Case 3: Requested range overlaps cache and extends beyond it
            source_start = local_cache_size
            source_end = end_byte

            if source_end is not None:
                source_range_header = f"bytes={source_start}-{source_end}"
            else:
                source_range_header = f"bytes={source_start}-"
            
            return await reverse_proxy(cache=cache, 
                                       url_task=alist_raw_url, 
                                       request_header={
                                           "Range": source_range_header                                           },
                                       response_headers=resp_header,
                                       client=client)
                
    if expected_status_code == 416:
        return fastapi.responses.Response(status_code=416, headers=resp_header)
    
    raise fastapi.HTTPException(status_code=500, detail=f"Unexpected argument: {expected_status_code}")

# for infuse
@app.get('/Videos/{item_id}/{filename}')
# for emby
@app.get('/videos/{item_id}/{filename}')
@app.get('/emby/Videos/{item_id}/{filename}')
@app.get('/emby/videos/{item_id}/{filename}')
async def redirect(item_id, filename, request: fastapi.Request, background_tasks: fastapi.BackgroundTasks):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx
    
    api_key = extract_api_key(request)
    media_source_id = request.query_params.get('MediaSourceId') if 'MediaSourceId' in request.query_params else request.query_params.get('mediaSourceId')

    if not media_source_id:
        raise fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")

    file_info: FileInfo = await get_file_info(item_id, media_source_id, api_key, client=app.requests_client)
    # host_url example: https://emby.example.com:8096/
    host_url = str(request.base_url)
    request_info = RequestInfo(file_info=file_info, host_url=host_url)
    
    logger.info(f"Requested Item ID: {item_id}")
    logger.info("MediaFile Mount Path: " + file_info.path)
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info.path):
        # 拼接完整的URL，如果query为空则不加问号
        redirected_url = f"{host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)
    
    alist_path = transform_file_path(file_info.path)
    file_info.path = alist_path
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enable_cache:
        return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)

    range_header = request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        logger.warning("Range header is not correctly formatted.")
        logger.warning(f"Request Headers: {request.headers}")
        return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)
    
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    if bytes_range.endswith('-'):
        start_byte = int(bytes_range[:-1])
        end_byte = None
    else:
        start_byte, end_byte = map(int, bytes_range.split('-'))
        
    logger.debug("Request Range Header: " + range_header)
    request_info.start_byte = start_byte
    request_info.end_byte = end_byte
    
    if start_byte >= file_info.size:
        logger.warning("Requested Range is out of file size.")
        return request_handler(expected_status_code=416, request_info=request_info, resp_header={'Content-Range': f'bytes */{file_info.size}'})

    cache_file_size = file_info.cache_file_size
    
    # 应该走缓存的情况1：请求文件开头
    if start_byte < cache_file_size:
        if end_byte is None or end_byte > cache_file_size:
            request_info.cache_status = CacheStatus.PARTIAL
        else:
            request_info.cache_status = CacheStatus.HIT
            
        # 如果请求末尾在cache范围内
        # 如果请求末尾在缓存文件大小之外，取缓存文件大小；否则取请求末尾
        cache_end_byte = cache_file_size if request_info.cache_status == CacheStatus.PARTIAL else end_byte
        resp_end_byte = file_info.size - 1 if end_byte is None or end_byte > cache_end_byte else cache_end_byte
        
        if get_cache_status(request_info):
            
            resp_headers = {
                'Content-Type': get_content_type(file_info.container),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info.size}",
                'Content-Length': f'{resp_end_byte - start_byte + 1}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            
            logger.info("Cached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            
            return await request_handler(expected_status_code=206, cache=read_cache_file(request_info), request_info=request_info, resp_header=resp_headers, client=app.requests_client)
        else:
            # 后台任务缓存文件
            background_tasks.add_task(write_cache_file, item_id, request_info, request.headers, client=app.requests_client)
            logger.info("Started background task to write cache file.")

            # 重定向到原始URL
            return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)
     
    # 应该走缓存的情况2：请求文件末尾
    elif file_info.size - start_byte < 2 * 1024 * 1024:
        request_info.cache_status = CacheStatus.HIT_TAIL
        
        if get_cache_status(request_info):
            if end_byte is None:
                resp_end_byte = file_info.size - 1
                resp_file_size = (resp_end_byte + 1) - start_byte
            else:
                resp_end_byte = end_byte
                resp_file_size = end_byte - start_byte + 1

            resp_headers = {
                'Content-Type': get_content_type(file_info.container),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info.size}",
                'Content-Length': f'{resp_file_size}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            
            logger.info("Cached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            logger.debug("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info.size}")
            logger.debug("Response Content-Length: " + f'{resp_file_size}')
            return fastapi.responses.StreamingResponse(read_cache_file(request_info), headers=resp_headers, status_code=206)
        else:
            # 后台任务缓存文件
            background_tasks.add_task(write_cache_file, item_id=item_id, request_info=request_info, req_header=request.headers, client=app.requests_client)
            logger.info("Started background task to write cache file.")

            # 重定向到原始URL
            return await request_handler(expected_status_code=302, request_info=request_info, client=app.requests_client)
    else:
        request_info.cache_status = CacheStatus.MISS
        
        resp_headers = {
            'Content-Type': get_content_type(file_info.container),
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start_byte}-{file_info.size - 1}/{file_info.size}',
            'Content-Length': f'{file_info.size - start_byte}',
            'Cache-Control': 'private, no-transform, no-cache',
            'X-EmbyToAList-Cache': 'Miss',
        }
        
        # 这里用206是因为响应302后vlc可能会出bug，不会跟随重定向，而是继续无限重复请求
        return await request_handler(expected_status_code=206, request_info=request_info, resp_header=resp_headers, client=app.requests_client)

if __name__ == "__main__":
    try:
        log_level = log_level.lower()
    except NameError:
        logger.warning("Log level not set in config.py, defaulting to INFO")
        log_level = "info"
    uvicorn.run(app, port=60001, host='0.0.0.0', log_config="logger_config.json", log_level=log_level)