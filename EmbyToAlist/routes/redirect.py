import fastapi
from loguru import logger

from ..config import CACHE_ENABLE, INITIAL_CACHE_SIZE_OF_TAIL, HIGH_COMPAT_MEDIA_CLIENTS, RAW_LINK_PROVIDER
from ..models import FileInfo, ItemInfo, RequestInfo, CacheRangeStatus, RangeInfo, response_headers_template
from ..utils.path import check_file_path
from ..utils.network import stream_handler, temporary_redirect
from ..utils.common import get_content_type, extract_api_key, ClientManager
from ..providers.manager import RawLinkManager
from ..providers.media_server.emby.items import get_item_info, get_file_info
from ..providers.media_server.emby.client import EmbyClient
from ..cache.manager import AppContext

router = fastapi.APIRouter()

# for infuse
@router.get('/Videos/{item_id}/{filename}')
# for emby
@router.get('/videos/{item_id}/{filename}')
@router.get('/emby/Videos/{item_id}/{filename}')
@router.get('/emby/videos/{item_id}/{filename}')
@router.head('/Videos/{item_id}/{filename}')
async def redirect(item_id, filename, request: fastapi.Request):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx
    
    api_key = extract_api_key(request)
    # jellyfin use mediaSourceId, emby use MediaSourceId
    media_source_id = request.query_params.get('MediaSourceId') if 'MediaSourceId' in request.query_params else request.query_params.get('mediaSourceId')
    user_id = request.query_params.get('userId')

    if not media_source_id:
        raise fastapi.HTTPException(status_code=400, detail="MediaSourceId is required")
    
    emby_client = EmbyClient(api_key=api_key)

    file_info: FileInfo = await get_file_info(emby_client, item_id, media_source_id)
    item_info: ItemInfo = await get_item_info(emby_client, item_id, user_id)
    
    logger.info(f"Requested Item ID: {item_id}")
    logger.info("MediaFile Mount Path: " + file_info.path)
    logger.debug("Request Headers: " + str(request.headers))        

    result = check_file_path(file_info)
    if not result.valid:
        redirected_url = f"{request.base_url}preventRedirect{request.url.path}{'?' + request.url.query if request.url.query else ''}"
        logger.info("Redirected Url: " + redirected_url)
        return fastapi.responses.RedirectResponse(url=redirected_url, status_code=307)
    else:
        file_info = result.transformed_file_info


    # 如果满足alist直链条件，提前通过异步缓存alist直链
    raw_link_manager = RawLinkManager(file_info.path, is_strm=file_info.is_strm, ua=request.headers.get('user-agent'))
    await raw_link_manager.create_task()
    
    if not CACHE_ENABLE:
        return await temporary_redirect(
            raw_link_manager=raw_link_manager,
        )

    range_header = request.headers.get('Range')
    if not range_header:
        logger.warning("Range header not found")
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
        api_key=api_key,
        range_info=range_info,
        user_agent=request.headers.get('User-Agent'),
    )
    
    ua_lower = (request.headers.get('User-Agent') or '').lower()
    if any(player in ua_lower for player in HIGH_COMPAT_MEDIA_CLIENTS):
        request_info.is_HIGH_COMPAT_MEDIA_CLIENTS = True
    
    cache_system = AppContext.get_cache_system()
    cache_exist = await cache_system.get_cache_status(request_info)
    
    # 应该走缓存的情况1：请求文件开头
    cache_file_size = file_info.cache_file_size
    if start_byte < cache_file_size:
        logger.debug("Match cache condition 1: Requesting file start")
        request_info.range_info.cache_range = (0, cache_file_size - 1)
        
        # check video player
        if request_info.is_HIGH_COMPAT_MEDIA_CLIENTS:
            response_end = cache_file_size - 1
        else:
            response_end = file_info.size - 1
            
        if end_byte is None or end_byte > cache_file_size:
            # 后续进行缓存拼接
            request_info.cache_range_status = CacheRangeStatus.PARTIALLY_CACHED
        else:
            # 完全在缓存范围内，直接响应缓存文件
            request_info.cache_range_status = CacheRangeStatus.FULLY_CACHED
            
            if cache_exist:
                resp_header = response_headers_template.copy()
                resp_header['Content-Type'] = get_content_type(file_info.name)
                resp_header['X-EmbyToAList-Cache'] = 'Hit'
                return fastapi.responses.StreamingResponse(
                    await cache_system.get_cache_file(request_info),
                    headers=resp_header,
                    status_code=206,
                )
            
    # 应该走缓存的情况2：请求文件末尾
    elif file_info.size - start_byte < INITIAL_CACHE_SIZE_OF_TAIL:
        logger.debug("Match cache condition 2: Requesting file tail")
        request_info.cache_range_status = CacheRangeStatus.FULLY_CACHED_TAIL
        # 默认初始缓存 1MB，之后根据请求头裁切 (TODO)
        request_info.range_info.cache_range = (start_byte, file_info.size - 1)
        if cache_exist:
                resp_header = response_headers_template.copy()
                resp_header['Content-Type'] = get_content_type(file_info.name)
                resp_header['X-EmbyToAList-Cache'] = 'Hit'
                resp_header['Content-Length'] = f'{file_info.size - start_byte}'
                resp_header['Content-Range'] = f"bytes {start_byte}-{file_info.size - 1}/{file_info.size}"
                return fastapi.responses.StreamingResponse(
                    await cache_system.get_cache_file(request_info),
                    headers=resp_header,
                    status_code=206,
                )
        response_end = file_info.size - 1
        
    else:
        logger.debug("Not match any cache condition")
        request_info.cache_range_status = CacheRangeStatus.NOT_CACHED
        return await temporary_redirect(
            raw_link_manager=raw_link_manager,
        )
        
    response_start = start_byte
    request_info.range_info.response_range = (response_start, response_end)

    response_headers = response_headers_template.copy()
    response_headers['Content-Type'] = get_content_type(file_info.container)
    response_headers['Content-Range'] = f"bytes {response_start}-{response_end}/{file_info.size}"
    response_headers['Content-Length'] = f'{response_end - response_start + 1}'
    # response_headers['Content-Disposition'] = f'inline; filename="{file_info.name}.{file_info.container}"'
    
    # consider head request
    if request.method == 'HEAD':
        return fastapi.responses.Response(
            headers=response_headers,
            status_code=206,
        )
    
    if cache_exist:
        return await stream_handler(
            cache=await cache_system.get_cache_file(request_info),
            response_headers=response_headers,
            request_info=request_info,
        )
        
    else:
        return await stream_handler(
            cache=None,
            response_headers=response_headers,
            request_info=request_info,
        )
