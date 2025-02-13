import fastapi
from loguru import logger

from ..config import CACHE_ENABLE
from ..models import FileInfo, ItemInfo, RequestInfo, CacheRangeStatus, RangeInfo, response_headers_template
from ..utils.path import should_redirect_to_alist
from ..utils.helpers import extract_api_key, get_content_type, RawLinkManager
from ..utils.network import reverse_proxy, temporary_redirect
from ..api.emby import get_item_info, get_file_info
from ..cache.CacheManager import CacheManager

router = fastapi.APIRouter()

# for infuse
@router.get('/Videos/{item_id}/{filename}')
# for emby
@router.get('/videos/{item_id}/{filename}')
@router.get('/emby/Videos/{item_id}/{filename}')
@router.get('/emby/videos/{item_id}/{filename}')
async def redirect(item_id, filename, request: fastapi.Request, background_tasks: fastapi.BackgroundTasks):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx
    
    api_key = extract_api_key(request)
    # jellyfin use mediaSourceId, emby use MediaSourceId
    media_source_id = request.query_params.get('MediaSourceId') if 'MediaSourceId' in request.query_params else request.query_params.get('mediaSourceId')

    if not media_source_id:
        raise fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")
    
    file_info: FileInfo = await get_file_info(item_id, api_key, media_source_id)
    item_info: ItemInfo = await get_item_info(item_id, api_key)
    host_url = str(request.base_url)
    
    logger.info(f"Requested Item ID: {item_id}")
    logger.info("MediaFile Mount Path: " + file_info.path)
    logger.debug("Request Headers: " + str(request.headers))
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info.path):
        # 拼接完整的URL，如果query为空则不加问号
        redirected_url = f"{host_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=302)
    
    # 如果满足alist直链条件，提前通过异步缓存alist直链
    raw_link_manager = RawLinkManager(file_info.path, is_strm=file_info.is_strm, ua=request.headers.get('user-agent'))
    await raw_link_manager.create_task()
    
    if not CACHE_ENABLE:
        return await temporary_redirect(
            raw_link_manager=raw_link_manager,
        )
    
    range_header = request.headers.get('Range')
    if not range_header:
        logger.debug("Range header not found")
        logger.debug(f"Request Headers: {request.headers}")
        
        return await temporary_redirect(
            raw_link_manager=raw_link_manager,
        )
        
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    if bytes_range.endswith('-'):
        start_byte = int(bytes_range[:-1])
        end_byte = None
    else:
        start_byte, end_byte = map(int, bytes_range.split('-'))
        
    logger.debug("Request Range Header: " + range_header)
    
    if start_byte >= file_info.size:
        logger.warning("Requested Range Not Satisfiable")
        raise fastapi.HTTPException(status_code=416, detail="Requested Range Not Satisfiable", headers={"Content-Range": f"bytes */{file_info.size}"})
    
    range_info = RangeInfo(
        request_range=(start_byte, end_byte),
        cache_range=None,
        response_range=None,
    )
    
    request_info = RequestInfo(
        file_info=file_info,
        item_info=item_info,
        raw_link_manager=raw_link_manager,
        cache_range_status=None,
        range_info=range_info,
    )
    
    cache_system = CacheManager.get_cache_system()
    cache_exist = cache_system.get_cache_status(request_info)
    
    # 应该走缓存的情况1：请求文件开头
    cache_file_size = file_info.cache_file_size
    if start_byte < cache_file_size:
        request_info.range_info.cache_range = (0, cache_file_size - 1)
        
        if end_byte is None or end_byte < cache_file_size:
            request_info.cache_range_status = CacheRangeStatus.PARTIALLY_CACHED
        else:
            request_info.cache_range_status = CacheRangeStatus.FULLY_CACHED
            if cache_exist:
                resp_header = response_headers_template.copy()
                resp_header['Content-Type'] = get_content_type(file_info.name)
                resp_header['X-EmbyToAList-Cache'] = 'Hit'
                return fastapi.responses.StreamingResponse(
                    cache_system.read_cache_file(request_info),
                    headers=resp_header,
                    status_code=206,
                )
            
    # 应该走缓存的情况2：请求文件末尾
    elif file_info.size - start_byte < 2*1024*1024:
        request_info.cache_range_status = CacheRangeStatus.FULLY_CACHED_TAIL
        request_info.range_info.cache_range = (start_byte, file_info.size - 1)
        if cache_exist:
                resp_header = response_headers_template.copy()
                resp_header['Content-Type'] = get_content_type(file_info.name)
                resp_header['X-EmbyToAList-Cache'] = 'Hit'
                return fastapi.responses.StreamingResponse(
                    cache_system.read_cache_file(request_info),
                    headers=resp_header,
                    status_code=206,
                )
        
    else:
        request_info.cache_range_status = CacheRangeStatus.NOT_CACHED
        return await temporary_redirect(
            raw_link_manager=raw_link_manager,
        )

    response_start = start_byte
    response_end = file_info.size - 1 if end_byte is None else end_byte
    request_info.range_info.response_range = (response_start, response_end)
    
    source_request_headers = {
        'User-Agent': request.headers.get('User-Agent'),
    }
    response_headers = response_headers_template.copy()
    response_headers['Content-Type'] = get_content_type(file_info.container)
    response_headers['Content-Range'] = f"bytes {response_start}-{response_end}/{file_info.size}"
    response_headers['Content-Length'] = f'{response_end - response_start + 1}'
    
    if cache_exist:
        source_request_headers['Range'] = f"bytes={cache_file_size}-{end_byte if end_byte is not None else ''}"
        
        return await reverse_proxy(
            cache=cache_system.read_cache_file(request_info),
            request_header=source_request_headers,
            response_headers=response_headers_template,
            request_info=request_info,
        )
        
    else:
        source_request_headers['Range'] = f"bytes={start_byte}-{end_byte if end_byte is not None else ''}"
        
        return await reverse_proxy(
            cache=None,
            request_header=source_request_headers,
            response_headers=response_headers,
            request_info=request_info,
        )