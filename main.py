from config import *
from components.utils import *
from components.cache import *
from datetime import datetime
import fastapi
import uvicorn
import httpx
from contextlib import asynccontextmanager

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
    print(f"{get_current_time()} - Requested Info URL: {url}")
    req = await client.get(url)
    req = req.json()
    if req is None: 
        print(f"{get_current_time()} - Error: failed to get file info")
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
async def redirect_to_alist_raw_url(file_path, host_url, client=httpx.AsyncClient) -> fastapi.Response:
    """创建Alist Raw Url并重定向"""
    cache_key = file_path + host_url
    if cache_key in URL_CACHE.keys():
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[cache_key]['time'] < 300:
            print("\nAlist Raw URL Cache exists and is valid (less than 5 minutes)")
            print("Redirected Url: " + URL_CACHE[cache_key]['url'])
            return fastapi.responses.RedirectResponse(url=URL_CACHE[cache_key]['url'], status_code=302)
        else:
            print("\nAlist Raw URL Cache is expired, re-fetching...")
            del URL_CACHE[cache_key]
    
    raw_url, code = await get_alist_raw_url(file_path, host_url=host_url, client=client)
    
    if code == 200:
        URL_CACHE[cache_key] = {
            'url': raw_url,
            'time': datetime.now().timestamp()
            }
        print("Redirected Url: " + raw_url)
        return fastapi.responses.RedirectResponse(url=raw_url, status_code=302)
    else:
        print(f"Error: failed to get Alist Raw Url, {code}")
        print(f"{raw_url}")
        return fastapi.HTTPException(status=code, detail=raw_url)
    

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
        return fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")

    file_info = await get_file_info(item_id, media_source_id, api_key, client=app.requests_client)
    # host_url example: https://emby.example.com:8096/
    host_url = str(request.base_url)
    
    if file_info['Status'] == "Error":
        print(file_info['Message'])
        return fastapi.HTTPException(status_code=500, detail=file_info['Message'])
    
    
    print(f"\n{get_current_time()} - Requested Item ID: {item_id}")
    print("MediaFile Mount Path: " + file_info.get('Path', 'Unknown'))
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info['Path']):
        # 拼接完整的URL，如果query为空则不加问号
        redirected_url = f"{host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        print("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)
    
    alist_path = transform_file_path(file_info['Path'])
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enable_cache:
        return await redirect_to_alist_raw_url(alist_path, host_url, client=app.requests_client)

    range_header = request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        print("\nWarning: Range header is not correctly formatted.")
        print(request.headers)
        return await redirect_to_alist_raw_url(alist_path, host_url, client=app.requests_client)
    
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    # start_byte = int(bytes_range.split('-')[0])
    if bytes_range.endswith('-'):
        start_byte = int(bytes_range[:-1])
        end_byte = None
    else:
        start_byte, end_byte = map(int, bytes_range.split('-'))
        
    print("Request Range Header: " + range_header)
    
    if start_byte >= file_info['Size']:
        print("\nWarning: Requested Range is out of file size.")
        return fastapi.responses.Response(status_code=416, headers={'Content-Range': f'bytes */{file_info["Size"]}'})

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
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            
            raw_url, code = await get_alist_raw_url(alist_path, host_url=host_url, client=app.requests_client)

            
            # return fastapi.responses.StreamingResponse(read_cache_file(item_id, alist_path, start_byte, cacheFileSize), headers=resp_headers, status_code=206)
            return await reverse_proxy(cache=read_cache_file(item_id, alist_path, start_byte, cache_end_byte), url=raw_url, response_headers=resp_headers, range=(start_byte, end_byte, cacheFileSize), client=app.requests_client)
        else:
            print("Request Range Header: " + range_header)
            # 后台任务缓存文件
            background_tasks.add_task(write_cache_file, item_id, alist_path, request.headers, cacheFileSize, start_byte, file_size=None, host_url=host_url, client=app.requests_client)
            print(f"{get_current_time()}: Started background task to write cache file.")

            # 重定向到原始URL
            return await redirect_to_alist_raw_url(alist_path, host_url, client=app.requests_client)
     
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
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            print("Request Range Header: " + range_header)
            print("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}")
            print("Response Content-Length: " + f'{resp_file_size}')
            return fastapi.responses.StreamingResponse(read_cache_file(item_id=item_id, path=alist_path, start_point=start_byte, end_point=end_byte), headers=resp_headers, status_code=206)
        else:
            print("Request Range Header: " + range_header)
            # 后台任务缓存文件
            background_tasks.add_task(write_cache_file, item_id=item_id, path=alist_path, req_header=request.headers, cache_size=0, start_point=start_byte, file_size=file_info['Size'], host_url=host_url, client=app.requests_client)
            print(f"{get_current_time()}: Started background task to write cache file.")

            # 重定向到原始URL
            return await redirect_to_alist_raw_url(alist_path, host_url, client=app.requests_client)
    else:
        
        resp_headers = {
            'Content-Type': get_content_type(file_info['Container']),
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start_byte}-{file_info["Size"] - 1}/{file_info["Size"]}',
            'Content-Length': f'{file_info["Size"] - start_byte}',
            'Cache-Control': 'private, no-transform, no-cache',
            'X-EmbyToAList-Cache': 'Miss',
        }
        
        raw_url, code = await get_alist_raw_url(alist_path, host_url=host_url, client=app.requests_client)

        
        # return await redirect_to_alist_raw_url(alist_path, host_url, client=app.requests_client)
        return await reverse_proxy(cache=None, url=raw_url, response_headers=resp_headers, range=(start_byte, end_byte, cacheFileSize), client=app.requests_client)


@app.post('/emby/webhook')
def webhook():
    pass

if __name__ == "__main__":
    uvicorn.run(app, port=60001, host='0.0.0.0')