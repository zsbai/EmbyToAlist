from ....models import FileInfo, ItemInfo, TVShowsInfo


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
        series_id=int(item.get('SeriesId', 0)),
        season_id=int(item.get('SeasonId', 0)),
        index_number=int(item.get('IndexNumber', 0))
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
