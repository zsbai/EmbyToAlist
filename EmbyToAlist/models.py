from dataclasses import dataclass
from enum import StrEnum

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .utils.helpers import RawLinkManager

class CacheStatus(StrEnum):
    """ 本地缓存状态 """
    
    HIT = "Hit"
    """ 缓存完全命中 """
    MISS = "Miss"
    """ 缓存未命中 """
    PARTIAL = "Partial"
    """ 缓存部分命中，响应内容拓展至缓存外 """
    HIT_TAIL = "Hit_Tail"
    """ 缓存完全命中，且请求在文件末尾2MB内 """
    UNKNOWN = "Unknown"
    """ 未知状态 """

@dataclass
class ItemInfo:
    """ 视频信息，如果type是movie，则season_id为None """
    
    item_id: int
    item_type: str
    """episode or movie"""
    tvshows_info: Optional['TVShowsInfo'] = None
    """ 剧集信息, 如果type是movie，则为None """
    
@dataclass
class TVShowsInfo:
    """ 剧集信息 """
    
    series_id: int
    """ 剧集id"""
    season_id: int
    """ 季id """
    index_number: int
    """ 剧集在季中的顺序 """
    

@dataclass
class FileInfo:
    path: str
    bitrate: int
    size: int
    container: str
    cache_file_size: int
    name: str
    is_strm: bool = False
    
@dataclass
class RequestInfo:
    file_info: FileInfo
    item_info: ItemInfo
    host_url: str
    start_byte: Optional[int] = None
    end_byte: Optional[int] = None
    cache_status: CacheStatus = CacheStatus.UNKNOWN
    api_key: Optional[str] = None
    raw_link_manager: Optional['RawLinkManager'] = None
    headers: Optional[dict] = None