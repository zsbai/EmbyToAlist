from ....models import FileInfo, ItemInfo, TVShowsInfo


def build_item_info(item: dict) -> ItemInfo:
  """构建ItemInfo对象
  Args:
    item (dict): Emby API返回的items信息
  Returns:
    ItemInfo: 构建的ItemInfo对象
  """

  # 获取原始类型
  raw_type = item.get('Type', '').lower()
  item_type = raw_type

  tvshows_info = None

  # 逻辑修正：
  # 只有当明确存在 SeriesId 时，才尝试构建 TVShowsInfo
  # 这避免了对非剧集视频（如 HomeVideo/Extras）强制读取 IndexNumber 导致的崩溃
  if 'SeriesId' in item:
    item_type = 'episode'
    tvshows_info = TVShowsInfo(
      # 使用 .get(key, 0) 提供默认值，防止 Key 不存在时报错
      series_id=int(item.get('SeriesId', 0)),
      season_id=int(item.get('SeasonId', 0)),
      index_number=int(item.get('IndexNumber', 0))
    )
  
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