from httpx import AsyncClient
from fastapi import HTTPException
from uvicorn.server import logger

from ..config import EMBY_SERVER
from ..models import ItemInfo, FileInfo
from ..utils import transform_file_path

async def get_item_info(item_id, api_key, client) -> ItemInfo:
    item_info_api = f"{EMBY_SERVER}/emby/Items?api_key={api_key}&Ids={item_id}"
    logger.debug(f"Requesting Item Info: {item_info_api}")
    try:
        req = await client.get(item_info_api)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Error: get_item_info failed, {e}")
        raise HTTPException(status_code=500, detail="Failed to request Emby server, {e}")
    
    if not req['Items']: 
        logger.debug(f"Item not found: {item_id};")
        return None
    
    item_type = req['Items'][0]['Type'].lower()
    if item_type != 'movie': item_type = 'episode'
    season_id = int(req['Items'][0]['SeasonId']) if item_type == 'episode' else None

    return ItemInfo(
        item_id=int(item_id),
        item_type=item_type,
        season_id=season_id
    )
    
# used to get the file info from emby server
async def get_file_info(item_id, api_key, media_source_id, client: AsyncClient) -> FileInfo:
    """
    从Emby服务器获取文件信息
    
    :param item_id: Emby Item ID
    :param MediaSourceId: Emby MediaSource ID
    :param apiKey: Emby API Key
    :return: 包含文件信息的字典
    """
    media_info_api = f"{EMBY_SERVER}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    logger.info(f"Requested Info URL: {media_info_api}")
    try:
        media_info = await client.get(media_info_api)
        media_info.raise_for_status()
        media_info = media_info.json()
    except Exception as e:
        logger.error(f"Error: failed to request Emby server, {e}")
        raise HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")

    if media_source_id is None:
        all_source = []
        for i in media_info['MediaSources']:
            all_source.append(FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            ))
        return all_source

    for i in media_info['MediaSources']:
        if i['Id'] == media_source_id:
            return FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            )
    # can't find the matched MediaSourceId in MediaSources
    raise HTTPException(status_code=500, detail="Can't match MediaSourceId")