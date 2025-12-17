import os
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
# @router.get('/emby/Users//Items/')
# Example Path: /emby/Items/xxx/PlaybackInfo
@router.get('/emby/Items/{item_id}/PlaybackInfo')
@router.post('/emby/Items/{item_id}/PlaybackInfo')
async def playback_info(item_id: str, request: fastapi.Request):
  logger.debug(f"Received request for PlaybackInfo with item_id: {item_id}")
  api_key = extract_api_key(request)
  emby_client = EmbyClient(api_key=api_key, client=ClientManager.get_client())
  params = dict(request.query_params)

  try:
    # 转发请求给 Emby
    json_body = None
    content = None
    try:
      json_body = await request.json()
    except Exception:
      content = await request.body()

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
    return fastapi.Response(content="Internal Server Error", status_code=500)

  # 修改 PlaybackInfo
  files_info: list[FileInfo] = [
    build_playback_info(ms) for ms in data.get('MediaSources', [])
  ]

  for index, each in enumerate(files_info):
    ms_data = data['MediaSources'][index]
    
    # 针对 strm 或 需要走 alist 的路径
    if each.is_strm or should_redirect_to_alist(each.path):
      
      # 预热缓存
      if not ENABLE_UA_PASSTHROUGH: 
        path = transform_file_path(each.path) if not each.is_strm else each.path
        try:
            raw_link_manager = RawLinkManager(path, each.is_strm, request.headers.get("User-Agent"))
            await raw_link_manager.create_task()
        except Exception as e:
            logger.warning(f"Failed to create raw link task: {e}")

      # 构造 URL
      ms_id = ms_data.get('Id')
      
      # 优先使用 X-Forwarded-Proto 防止反代后协议错误
      scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
      host = request.headers.get("host") or request.url.netloc
      
      # 伪装文件名，确保是主流视频格式后缀，防止客户端因为 .strm 后缀拒绝播放
      filename = os.path.basename(each.path)
      if not filename or filename.lower().endswith('.strm'):
        filename = "stream.mkv" 

      # 安全处理 api_key
      safe_api_key = api_key if api_key else ""

      new_url = f"{scheme}://{host}/emby/videos/{item_id}/{filename}?MediaSourceId={ms_id}&Static=true&api_key={safe_api_key}"
      
      ms_data['DirectStreamUrl'] = new_url

      # 强制修改容器格式，解决 strm 被识别为文本或未知格式的问题
      current_container = ms_data.get('Container', '').lower()
      if current_container in ['strm', '', 'other']:
        ms_data['Container'] = 'mkv' # mkv 兼容性最好，让客户端尝试解析流
      
      # 强制开启直连，禁用转码
      ms_data['SupportsDirectPlay'] = True
      ms_data['SupportsDirectStream'] = True
      ms_data['SupportsTranscoding'] = False
      
      # 清理干扰项，防止客户端尝试转码
      ms_data.pop('TranscodingUrl', None)
      ms_data.pop('TranscodingSubProtocol', None)
      ms_data.pop('TranscodingContainer', None)
      
      logger.info(f"Fixed PlaybackInfo: Container={ms_data.get('Container')}, URL={new_url}")
    
    else:
      # 如果不走 Alist，保持原样或者根据需要处理
      pass

  headers = dict(response.headers)
  headers.pop('content-length', None)

  return fastapi.responses.JSONResponse(
    content=data,
    status_code=response.status_code,
    headers=headers,
  )