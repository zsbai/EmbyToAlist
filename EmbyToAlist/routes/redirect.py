import fastapi
from loguru import logger

from ..config import CACHE_ENABLE
from ..models import RequestInfo, CacheStatus, ItemInfo, FileInfo
from ..utils.helpers import extract_api_key, get_content_type, RawLinkManager
from ..utils.path import should_redirect_to_alist
from ..cache.media import read_cache_file, write_cache_file, get_cache_status
from ..utils.handler import request_handler
from ..api.emby import get_item_info, get_file_info

router = fastapi.APIRouter()

# for infuse
@router.get('/Videos/{item_id}/{filename}')
# for emby
@router.get('/videos/{item_id}/{filename}')
@router.get('/emby/Videos/{item_id}/{filename}')
@router.get('/emby/videos/{item_id}/{filename}')
async def redirect(item_id, filename, request: fastapi.Request, background_tasks: fastapi.BackgroundTasks):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx

    requests_client = request.app.state.requests_client
    
    api_key = extract_api_key(request)
    media_source_id = request.query_params.get('MediaSourceId') if 'MediaSourceId' in request.query_params else request.query_params.get('mediaSourceId')

    if not media_source_id:
        raise fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")

    file_info: FileInfo = await get_file_info(item_id, api_key, media_source_id, client=requests_client)
    item_info: ItemInfo = await get_item_info(item_id, api_key, client=requests_client)
    # host_url example: https://emby.example.com:8096/
    host_url = str(request.base_url)
    request_info = RequestInfo(
        file_info=file_info, 
        item_info=item_info, 
        host_url=host_url, 
        api_key=api_key,
        headers=request.headers
        )
    
    logger.info(f"Requested Item ID: {item_id}")
    logger.info("MediaFile Mount Path: " + file_info.path)
    logger.debug("Request Headers: " + str(request.headers))
    # logger.debug("Request Info: " + str(request_info))
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info.path):
        # 拼接完整的URL，如果query为空则不加问号
        redirected_url = f"{host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)
    
    # 如果满足alist直链条件，提前通过异步缓存alist直链
    raw_link_manager = RawLinkManager(file_info.path, request_info, requests_client)
    await raw_link_manager.create_task()
    request_info.raw_link_manager = raw_link_manager
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not CACHE_ENABLE:
        return await request_handler(
            expected_status_code=302,
            request_info=request_info,
            client=requests_client
            )

    range_header = request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        logger.warning("Range header is not correctly formatted.")
        logger.debug(f"Request Headers: {request.headers}")
        
        request_info.cache_status = CacheStatus.PARTIAL
        request_info.start_byte = 0
        
        if get_cache_status(request_info):
            logger.info("Cached file exists and is valid, response 200.")
            resp_headers = {
            'Cache-Control': 'private, no-transform, no-cache',
            'Content-Length': str(file_info.size),
            'X-EmbyToAList-Cache': 'Hit',
        }
            return await request_handler(
                expected_status_code=200,
                cache=read_cache_file(request_info),
                request_info=request_info,
                resp_header=resp_headers,
                background_tasks=background_tasks,
                client=requests_client
                )
        else:
            background_tasks.add_task(
                write_cache_file,
                item_id,
                request_info,
                request.headers,
                client=requests_client
                )

            logger.info("Started background task to write cache file.")
            
            return await request_handler(
                expected_status_code=302,
                request_info=request_info,
                client=requests_client
                )
        
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
        return request_handler(
            expected_status_code=416,
            request_info=request_info,
            resp_header={'Content-Range': f'bytes */{file_info.size}'}
            )

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
            
            return await request_handler(
                expected_status_code=206, 
                cache=read_cache_file(request_info), 
                request_info=request_info, 
                resp_header=resp_headers, 
                background_tasks=background_tasks, 
                client=requests_client
                )
        else:
            # 后台任务缓存文件
            background_tasks.add_task(
                write_cache_file,
                item_id,
                request_info,
                request.headers,
                client=requests_client
                )
            logger.info("Started background task to write cache file.")

            # 重定向到原始URL
            return await request_handler(
                expected_status_code=302,
                request_info=request_info,
                background_tasks=background_tasks,
                client=requests_client
                )
     
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
            return fastapi.responses.StreamingResponse(
                read_cache_file(request_info),
                headers=resp_headers,
                status_code=206
                )
        else:
            # 后台任务缓存文件
            background_tasks.add_task(
                write_cache_file, 
                item_id=item_id,
                request_info=request_info,
                req_header=request.headers,
                client=requests_client
                )
            logger.info("Started background task to write cache file.")

            # 重定向到原始URL
            return await request_handler(
                expected_status_code=302, 
                request_info=request_info,
                background_tasks=background_tasks,
                client=requests_client
                )
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
        return await request_handler(
            expected_status_code=206, 
            request_info=request_info, 
            resp_header=resp_headers, 
            background_tasks=background_tasks, 
            client=requests_client
            )