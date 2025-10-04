import fastapi
from loguru import logger

from ..utils.common import ClientManager, extract_api_key
from ..providers.media_server.emby.helpers import build_playback_info
from ..providers.manager import RawLinkManager
from ..utils.path import transform_file_path, should_redirect_to_alist
from ..models import FileInfo
from ..config import ENABLE_UA_PASSTHROUGH
from ..providers.media_server.emby.client import EmbyClient

router = fastapi.APIRouter()

# Example Path: /emby/Users/xxxx/Items/xxxx
# @router.get('/emby/Users/{user_id}/Items/{item_id}')
# Example Path: /emby/Items/xxx/PlaybackInfo
@router.get('/emby/Items/{item_id}/PlaybackInfo')
@router.post('/emby/Items/{item_id}/PlaybackInfo')
async def playback_info(item_id: str, request: fastapi.Request):
    logger.debug(f"Received request for PlaybackInfo with item_id: {item_id}")
    api_key = extract_api_key(request)
    emby_client = EmbyClient(api_key=api_key, client=ClientManager.get_client())
    
    params = dict(request.query_params)
    
    try:
        # Forward the request to the remote server
        # 尝试 JSON 负载，否则以原始 body 透传
        json_body = None
        content = None
        try:
            json_body = await request.json()
        except Exception:
            content = await request.body()

        # 过滤不该直传的请求头
        blocked = {"host", "content-length", "connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade"}
        fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in blocked}

        response = await emby_client.raw_request(
            method=request.method,
            path=f"/emby/Items/{item_id}/PlaybackInfo",
            params=params,
            headers=fwd_headers,
            json_body=json_body,
            content=content,
        )
        response.raise_for_status()
        
        data = response.json()
    except Exception as e:
        logger.error(f"Error during proxying request: {e}")
        return fastapi.Response(
            content="Internal Server Error",
            status_code=500,
        )
    # 从 PlaybackInfo 的 MediaSources 构建文件信息列表
    files_info: list[FileInfo] = [
        build_playback_info(ms) for ms in data.get('MediaSources', [])
    ]
    for index, each in enumerate(files_info):
        
        # 如果需要alist处理，如云盘路径，或strm流，提前通过异步缓存alist直链
        if should_redirect_to_alist(each.path) or each.is_strm:
            
            if not ENABLE_UA_PASSTHROUGH: 
                path = transform_file_path(each.path) if not each.is_strm else each.path
                
                raw_link_manager = RawLinkManager(path, each.is_strm, request.headers.get("User-Agent"))
                await raw_link_manager.create_task()
            
            redirected_url = None
        else:
            original_stream_url = data['MediaSources'][index]['DirectStreamUrl'] 
            redirected_url = f"{request.base_url}preventRedirect/emby{original_stream_url}"

        # if redirected_url:
        #     data['MediaSources'][index]['DirectStreamUrl'] = redirected_url
        #     logger.debug(f"Play Url modified to: {redirected_url}")
    
    # logger.debug(data)
    headers = dict(response.headers)
    headers.pop('content-length', None)
    
    # Prepare the response to forward back to the client
    return fastapi.responses.JSONResponse(
        content=data,
        status_code=response.status_code,
        headers=headers,
    )        
    # 如果满足alist直链条件，提前通过异步缓存alist直链
    
