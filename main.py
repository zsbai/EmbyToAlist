from contextlib import asynccontextmanager
from datetime import datetime

import fastapi
import httpx
import uvicorn
from uvicorn.server import logger

from config import *
from components.utils import *
from components.cache import *
from typing import Tuple

# 使用上下文管理器，创建异步请求客户端
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    app.requests_client = httpx.AsyncClient()
    yield
    await app.requests_client.aclose()

app = fastapi.FastAPI(lifespan=lifespan)

URL_CACHE = {}
    
# used to get the file info from emby server
async def get_file_info(item_id, MediaSourceId, apiKey, client: httpx.AsyncClient) -> dict:
    """
    从Emby服务器获取文件信息
    
    :param item_id: Emby Item ID
    :param MediaSourceId: Emby MediaSource ID
    :param apiKey: Emby API Key
    :return: 包含文件信息的字典
    """
    data = {}
    url = f"{emby_server}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={MediaSourceId}&api_key={apiKey}"
    # print(f"{get_current_time()} - Requested Info URL: {url}")
    logger.info(f"Requested Info URL: {url}")
    req = await client.get(url)
    req = req.json()
    if req is None: 
        # print(f"{get_current_time()} - Error: Failed to get file info")
        logger.error("Failed to get file info")
        return data
    for i in req['MediaSources']:
        # print(i)
        if i['Id'] == MediaSourceId:
            data['Status'] = "Success"
            data['Path'] = i['Path']
            # data['DirectStreamUrl'] = i['DirectStreamUrl']
            # data['Protocol'] = i['Protocol']
            data['Bitrate'] = i['Bitrate'] 
            data['Size'] = i['Size']
            data['Container'] = i['Container']
            return data
    
    data['Status'] = "Error"
    data['Message'] = "Can't match MediaSourceId"
    return data

# return Alist Raw Url
@get_time
async def get_or_cache_alist_raw_url(file_path, host_url, client=httpx.AsyncClient) -> Tuple[int, str]:
    """创建或获取Alist Raw Url缓存，缓存时间为5分钟"""
    cache_key = file_path + host_url
    if cache_key in URL_CACHE.keys():
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[cache_key]['time'] < 300:
            # print("\nAlist Raw URL Cache exists and is valid (less than 5 minutes)")
            # print("Redirected Url: " + URL_CACHE[cache_key]['url'])
            logger.info("Alist Raw URL Cache exists and is valid (less than 5 minutes)")
            logger.info("Redirected Url: " + URL_CACHE[cache_key]['url'])
            return 200, URL_CACHE[cache_key]['url']
        else:
            # print("\nAlist Raw URL Cache is expired, re-fetching...")
            logger.info("Alist Raw URL Cache is expired, re-fetching...")
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
async def request_handler(status_code: int,
                          cache: AsyncGenerator[bytes, None]=None,
                          file_path: str=None,
                          range_header: Tuple[int, int, int]=None,
                          host_url: str=None, 
                          resp_header: dict=None,
                          client: httpx.AsyncClient=None
                          ) -> fastapi.Response:
    """决定反代还是重定向，创建alist缓存
    
    :param status_code: 期望返回的状态码，302或206
    :param cache: 内部缓存数据
    :param file_path: alist文件路径
    :param range: (start_byte, end_byte, local_cache_size) 请求的起始和结束字节，以及缓存大小
    :param host_url: 请求头的host，用于alist直链
    :param resp_header: 需要返回的响应头
    :param client: httpx异步请求客户端
    
    :return fastapi.Response: 返回重定向或反代的响应
    """
    # 如果满足alist直链条件，提前通过异步缓存alist直链
    if host_url is not None and file_path is not None:
        alist_raw_url = asyncio.create_task(get_or_cache_alist_raw_url(file_path=file_path, host_url=host_url, client=client))
    
    if status_code == 302:
        code, raw_url = await alist_raw_url
        if code != 200:
            raise fastapi.HTTPException(status_code=500, detail=f"Get Alist Raw Url Error: {raw_url};\nCode: {code}")
        return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)
    
    if status_code == 206:
        start_byte, end_byte, local_cache_size = range_header

        if start_byte >= local_cache_size:
            # Case 1: Requested range is entirely beyond the cache
            # Prepare Range header
            if end_byte is not None:
                source_range_header = f"bytes={start_byte}-{end_byte - 1}"
            else:
                source_range_header = f"bytes={start_byte}-"
            
            code, raw_url = await alist_raw_url
            if code != 200: raise fastapi.HTTPException(status_code=500, detail=f"Get Alist Raw Url Error: {raw_url};\nCode: {code}")   
            
            return await reverse_proxy(cache=None, 
                                       url=raw_url, 
                                       request_header={
                                           "Range": source_range_header, 
                                           "Host": raw_url.split('/')[2]
                                           },
                                       response_headers=resp_header,
                                       client=client)
        elif end_byte is not None and end_byte <= local_cache_size:
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
            
            code, raw_url = await alist_raw_url
            if code != 200: 
                raw_url = None
                raise fastapi.HTTPException(status_code=500, detail=f"Get Alist Raw Url Error: {raw_url};\nCode: {code}")
            
            
            return await reverse_proxy(cache=cache, 
                                       url=raw_url, 
                                       request_header={
                                           "Range": source_range_header, 
                                           "Host": raw_url.split('/')[2]
                                           },
                                       response_headers=resp_header,
                                       client=client)
                
    if status_code == 416:
        return fastapi.responses.Response(status_code=416, headers=resp_header)
    
    raise fastapi.HTTPException(status_code=500, detail=f"Unexpected argument: {status_code}")

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

    file_info = await get_file_info(item_id, media_source_id, api_key, client=app.requests_client)
    # host_url example: https://emby.example.com:8096/
    host_url = str(request.base_url)
    
    if file_info['Status'] == "Error":
        # print(file_info['Message'])
        logger.error(file_info['Message'])
        raise fastapi.HTTPException(status_code=500, detail=file_info['Message'])
    
    
    # print(f"\n{get_current_time()} - Requested Item ID: {item_id}")
    # print("MediaFile Mount Path: " + file_info.get('Path', 'Unknown'))
    logger.info(f"Requested Item ID: {item_id}")
    logger.info("MediaFile Mount Path: " + file_info.get('Path', 'Unknown'))
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info['Path']):
        # 拼接完整的URL，如果query为空则不加问号
        redirected_url = f"{host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        # print("Redirected Url: " + redirected_url)
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)
    
    alist_path = transform_file_path(file_info['Path'])
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enable_cache:
        return await request_handler(status_code=302, file_path=alist_path, host_url=host_url, client=app.requests_client)

    range_header = request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        # print("\nWarning: Range header is not correctly formatted.")
        # print(request.headers)
        logger.warning("Range header is not correctly formatted.")
        logger.warning(f"Request Headers: {request.headers}")
        return await request_handler(status_code=302, file_path=alist_path, host_url=host_url, client=app.requests_client)
    
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    # start_byte = int(bytes_range.split('-')[0])
    if bytes_range.endswith('-'):
        start_byte = int(bytes_range[:-1])
        end_byte = None
    else:
        start_byte, end_byte = map(int, bytes_range.split('-'))
        
    # print("Request Range Header: " + range_header)
    logger.info("Request Range Header: " + range_header)
    
    if start_byte >= file_info['Size']:
        # print("\nWarning: Requested Range is out of file size.")
        logger.warning("Requested Range is out of file size.")
        return request_handler(status_code=416, resp_header={'Content-Range': f'bytes */{file_info["Size"]}'})

    # 获取缓存15秒的文件大小， 并取整
    cacheFileSize = int(file_info.get('Bitrate', 27962026) / 8 * 15)
    
    # 应该走缓存的情况1：请求文件开头
    if start_byte < cacheFileSize:
        
        # 如果请求末尾在cache范围内
        cache_end_byte = cacheFileSize if end_byte is None or end_byte > cacheFileSize else end_byte
        resp_end_byte = file_info['Size'] - 1 if end_byte is None or end_byte > cache_end_byte else cache_end_byte
        
        if get_cache_status(item_id, alist_path, start_byte):
            
            resp_headers = {
                'Content-Type': get_content_type(file_info['Container']),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}",
                'Content-Length': f'{resp_end_byte - start_byte + 1}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            
            # print("\nCached file exists and is valid")
            logger.info("Cached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            
            # return await reverse_proxy(cache=read_cache_file(item_id, alist_path, start_byte, cache_end_byte), alist_params=(alist_path, host_url), response_headers=resp_headers, range=(start_byte, end_byte, cacheFileSize), client=app.requests_client)
            return await request_handler(status_code=206, cache=read_cache_file(item_id, alist_path, start_byte, cache_end_byte), file_path=alist_path, range_header=(start_byte, end_byte, cacheFileSize), host_url=host_url, resp_header=resp_headers, client=app.requests_client)
        else:
            # 后台任务缓存文件
            background_tasks.add_task(write_cache_file, item_id, alist_path, request.headers, cacheFileSize, start_byte, file_size=None, host_url=host_url, client=app.requests_client)
            # print(f"{get_current_time()}: Started background task to write cache file.")
            logger.info("Started background task to write cache file.")

            # 重定向到原始URL
            return await request_handler(status_code=302, file_path=alist_path, host_url=host_url, client=app.requests_client)
     
    # 应该走缓存的情况2：请求文件末尾
    elif file_info['Size'] - start_byte < 2 * 1024 * 1024:
        if get_cache_status(item_id, path=alist_path, start_point=start_byte):
            if end_byte is None:
                resp_end_byte = file_info['Size'] - 1
                resp_file_size = (resp_end_byte + 1) - start_byte
            else:
                resp_end_byte = end_byte
                resp_file_size = end_byte - start_byte + 1

            resp_headers = {
                'Content-Type': get_content_type(file_info['Container']),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}",
                'Content-Length': f'{resp_file_size}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            
            # print("\nCached file exists and is valid")
            logger.info("Cached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            # print("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}")
            # print("Response Content-Length: " + f'{resp_file_size}')
            logger.debug("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}")
            logger.debug("Response Content-Length: " + f'{resp_file_size}')
            return fastapi.responses.StreamingResponse(read_cache_file(item_id=item_id, path=alist_path, start_point=start_byte, end_point=end_byte), headers=resp_headers, status_code=206)
        else:
            # 后台任务缓存文件
            background_tasks.add_task(write_cache_file, item_id=item_id, path=alist_path, req_header=request.headers, cache_size=0, start_point=start_byte, file_size=file_info['Size'], host_url=host_url, client=app.requests_client)
            # print(f"{get_current_time()}: Started background task to write cache file.")
            logger.info("Started background task to write cache file.")

            # 重定向到原始URL
            return await request_handler(status_code=302, file_path=alist_path, host_url=host_url, client=app.requests_client)
    else:
        
        resp_headers = {
            'Content-Type': get_content_type(file_info['Container']),
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start_byte}-{file_info["Size"] - 1}/{file_info["Size"]}',
            'Content-Length': f'{file_info["Size"] - start_byte}',
            'Cache-Control': 'private, no-transform, no-cache',
            'X-EmbyToAList-Cache': 'Miss',
        }
        
        return await request_handler(status_code=206, file_path=alist_path, range_header=(start_byte, end_byte, cacheFileSize), host_url=host_url, resp_header=resp_headers, client=app.requests_client)

if __name__ == "__main__":
    uvicorn.run(app, port=60001, host='0.0.0.0', log_config="logger_config.json")