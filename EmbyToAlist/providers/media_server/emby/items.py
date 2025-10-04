from loguru import logger

from ....models import ItemInfo, FileInfo
from .helpers import build_playback_info, build_item_info
from .client import EmbyClient
from typing import Optional

async def get_item_info(client: EmbyClient, item_id: str, user_id: Optional[str] = None) -> Optional[ItemInfo]:
    """获取某个Emby Item具体的信息

    Args:
        item_id (str): Emby Item ID
        api_key (str): Emby API Key
        user_id (str): Emby User ID
    Returns:
        ItemInfo: 包含Item信息的dataclass
        None: 如果没有找到Item
    """
    
    if user_id is None:
        user_id = ''

    data = await client.get_json("/emby/Items", params={
        "UserId": user_id,
        "Ids": item_id,
    })
    
    assert len(data['Items']) != 0, f"Item not found: {item_id};"
    
    return build_item_info(data['Items'][0])

async def get_series_info(client: EmbyClient, series_id: int, season_id: int) -> list[ItemInfo]:
    """获取剧集某一个季的所有Item信息

    Args:
        series_id (int): Emby Series ID，表示剧集（如一部电视剧）的唯一标识符，用于指定要查询的剧集
        season_id (int): Emby Season ID，表示该剧集下某一季的唯一标识符，用于限定只查询该季的剧集内容
        api_key (str): Emby API Key

    Returns:
        list[ItemInfo]: 包含Item信息的dataclass列表
    """
    # shows_info_api = f"{EMBY_SERVER}/emby/Shows/{series_id}/Episodes?SeasonId={season_id}&api_key={api_key}"
    data = await client.get_json(
        f"/emby/Shows/{series_id}/Episodes",
        params={"SeasonId": season_id},
    )
    
    return [build_item_info(i) for i in data.get('Items', [])]
        
async def get_next_episode_item_info(
    previous_item_info: ItemInfo,
    client: EmbyClient,
    ) -> ItemInfo | None:
    """获取剧集当前一季的下一集信息，并不会返回下一季的第一集

    Args:
        previous_item_info (ItemInfo): 当前集的Item信息
        api_key (str): Emby API Key

    Returns:
        ItemInfo: 包含Item信息的dataclass
        None: 如果没有找到下一集
    """
    # 统一用小写比较以兼容 build_item_info 的输出
    if (previous_item_info.item_type or '').lower() != 'episode':
        return None
    
    series_id = previous_item_info.tvshows_info.series_id
    season_id = previous_item_info.tvshows_info.season_id
    item_id = previous_item_info.item_id
    items: list[ItemInfo] = await get_series_info(client, series_id, season_id)
    
    for i in items:
        if i.item_id == item_id:
            index = i.tvshows_info.index_number
            # index_number 通常从 1 开始；items 列表为 0 基
            next_pos = index  # 下一个位置
            if next_pos >= len(items):
                return None
            return items[next_pos]
        

# used to get the file info from emby server
async def get_file_info(client: EmbyClient, item_id: str, media_source_id: Optional[str] = None) -> FileInfo | list[FileInfo]:
    """
    从Emby 的 PlaybackInfo 获取文件播放信息
    
    Args:
        item_id (str): Emby Item ID
        api_key (str): Emby API Key
        media_source_id (str): Emby MediaSource ID
    Returns:
        FileInfo: 包含文件信息的dataclass
        list[FileInfo]: 没有指定MediaSourceId时，返回所有文件信息的列表
    """
    # "{EMBY_SERVER}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    data = await client.get_json(
        f"/emby/Items/{item_id}/PlaybackInfo",
        params={"MediaSourceId": media_source_id},
    )
    
    if media_source_id is None:
        # 如果没有指定MediaSourceId，返回所有文件信息
        return [build_playback_info(i) for i in data['MediaSources']]   
    
    else:
        # 如果指定了MediaSourceId，返回单个文件信息
        for i in data['MediaSources']:
            if i['Id'] == media_source_id:
                return build_playback_info(i)
        
        # 如果没有找到指定的MediaSourceId，返回None
        return None
