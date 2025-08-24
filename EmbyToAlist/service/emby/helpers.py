import fastapi
from loguru import logger

from ...config import EMBY_SERVER
from ...models import FileInfo, ItemInfo, TVShowsInfo
from ...utils.common import ClientManager

async def request_emby_json(api_url: str) -> dict:
    """
    请求Emby API并返回JSON数据

    Args:
        api_url (str): Emby API的URL
    Returns:
        dict: 返回的JSON数据
    """
    client = ClientManager.get_client()
    try:
        logger.debug(f"Requesting URL: {api_url}")
        response = await client.get(api_url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to request Emby API: {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")


def emby_api(path: str, **params) -> str:
    """构建Emby API请求URL

    Args:
        path (str): Emby API路径
        **params: 其他查询参数
    Returns:
        str: 完整的Emby API请求URL
    """
    query = '&'.join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{EMBY_SERVER}{path}?{query}"
    logger.debug(f"Emby API query: {url}")
    return url


def build_item_info(item: dict) -> ItemInfo:
    """构建ItemInfo对象
    Args:
        item (dict): Emby API返回的items信息
    Returns:
        ItemInfo: 构建的ItemInfo对象
    """

    item_type = item.get('Type', '').lower()
    if item_type != 'movie':
        item_type = 'episode'

    tvshows_info = TVShowsInfo(
        series_id=int(item['SeriesId']),
        season_id=int(item['SeasonId']),
        index_number=int(item['IndexNumber'])
    ) if item_type == 'episode' else None

    return ItemInfo(
        item_id=int(item['Id']),
        item_type=item_type,
        in_progress=item.get('UserData', {}).get('PlaybackPositionTicks', 0) > 0,
        tvshows_info=tvshows_info
    )


def build_playback_info(data: dict) -> FileInfo:
    """解析 PlaybackInfo 返回的播放信息

    Args:
        data (dict): Emby PlaybackInfo 返回的json数据
    Returns:
        FileInfo: 包含文件信息的dataclass
    """

    return FileInfo(
        id=data.get('Id'),
        path=data.get('Path'),
        bitrate=data.get('Bitrate', 27962026),
        size=data.get('Size', 0),
        container=data.get('Container', None),
        # 获取15秒的缓存文件大小， 并取整
        cache_file_size=int(data.get('Bitrate', 27962026) / 8 * 20),
        name=data.get('Name'),
        # 是否为远程流
        is_strm=data.get('IsRemote', False)
    )