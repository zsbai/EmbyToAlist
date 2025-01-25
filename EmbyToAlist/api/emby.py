from httpx import AsyncClient
from fastapi import HTTPException
from loguru import logger

from ..config import EMBY_SERVER
from ..models import ItemInfo, FileInfo, TVShowsInfo
from ..utils.path import transform_file_path

async def get_item_info(item_id, api_key, client) -> ItemInfo:
    """获取Emby Item信息

    :param item_id: Emby Item ID
    :param api_key: Emby API Key
    :param client: HTTPX异步请求客户端
    
    :return: 包含Item信息的dataclass
    """
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
    
    if item_type == 'episode':
    
        tvshows_info = TVShowsInfo(
            series_id=int(req['Items'][0]['SeriesId']),
            season_id=int(req['Items'][0]['SeasonId']),
            index_number=int(req['Items'][0]['IndexNumber'])
        )
    else:
        tvshows_info = None

    return ItemInfo(
        item_id=int(item_id),
        item_type=item_type,
        tvshows_info=tvshows_info
    )

async def get_series_info(series_id: int, season_id: int, api_key: str, client: AsyncClient) -> list[ItemInfo]:
    """获取剧集某一个季的所有Item信息

    Args:
        series_id (int): Emby Series ID
        season_id (int): Emby Season ID
        api_key (str): Emby API Key
        client (AsyncClient): HTTPX异步请求客户端

    Returns:
        list[ItemInfo]: 包含Item信息的dataclass列表
    """
    shows_info_api = f"{EMBY_SERVER}/emby/Shows/{series_id}/Episodes?SeasonId={season_id}&api_key={api_key}"
    
    try:
        req = await client.get(shows_info_api)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Error: get_series_info failed, {e}")
        raise HTTPException(status_code=500, detail="Failed to request Emby server, {e}")
    
    items = []
    for i in req['Items']:
        items.append(ItemInfo(
            item_id=int(i['Id']),
            item_type='episode',
            tvshows_info=TVShowsInfo(
                series_id=series_id,
                season_id=season_id,
                index_number=int(i['IndexNumber'])
            )
        ))
    return items
        
async def get_next_episode_item_info(series_id: int, season_id: int, item_id: int, api_key: str, client: AsyncClient) -> ItemInfo | None:
    """获取剧集当前一季的下一集信息，并不会返回下一季的第一集

    Args:
        series_id (int): Emby Series ID
        season_id (int): Emby Season ID
        item_id (int): Emby Item ID
        api_key (str): Emby API Key
        client (AsyncClient): HTTPX异步请求客户端

    Returns:
        ItemInfo: 包含Item信息的dataclass
        None: 如果没有找到下一集
    """
    items: list[ItemInfo] = await get_series_info(series_id, season_id, api_key, client)
    for i in items:
        if i.item_id == item_id:
            index = i.tvshows_info.index_number
            if index == len(items):
                return None
            return items[index]
        

# used to get the file info from emby server
async def get_file_info(item_id, api_key, media_source_id, client: AsyncClient, media_info_api=None) -> FileInfo | list[FileInfo]:
    """
    从Emby服务器获取文件播放信息
    
    :param item_id: Emby Item ID
    :param MediaSourceId: Emby MediaSource ID
    :param apiKey: Emby API Key
    :param client: HTTPX异步请求客户端
    :param media_info_api: 自定义PlaybackInfo URL，及参数
    :return: 包含文件信息的dataclass
    """
    if media_info_api is None:
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
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15),
                name=i.get('Name'),
                # 是否为远程流
                is_strm=i.get('IsRemote', False)
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
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15),
                name=i.get('Name'),
                # 是否为远程流
                is_strm=i.get('IsRemote', False)
            )
    # can't find the matched MediaSourceId in MediaSources
    raise HTTPException(status_code=500, detail="Can't match MediaSourceId")