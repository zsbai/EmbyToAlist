import asyncio
import copy
from pathlib import Path

from loguru import logger

from ..config import INITIAL_CACHE_SIZE_OF_TAIL, MEMORY_CACHE_ONLY
from ..models import FileInfo, RequestInfo, CacheRangeStatus
from .manager import AppContext
from ..utils.common import ClientManager
from ..cache.writer import ChunksWriter
from ..cache.storage.file_storage import FileStorage
from ..service.emby.items import get_next_episode_item_info
from ..service.manager import RawLinkManager
from typing import AsyncGenerator, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    import httpx
    
class CacheSystem():
    VERSION: str = "1.0.1"
    def __init__(self, root_dir: str):
        self.root_dir: Path = Path(root_dir)
        self.condition = asyncio.Condition()
        self.client: httpx.AsyncClient = ClientManager.get_client()
        self.storage = FileStorage(self.root_dir, self.VERSION)
        
        self.task_manager = AppContext.get_task_manager()
        
        self._initialize()
            
    def _initialize(self):
        """初始化缓存系统
        """
        pass
                
    def shutdown(self):
        pass
    
    async def get_writer(self, request_info: RequestInfo) -> ChunksWriter:
        """
        获取缓存文件的写入器, 如果缓存文件已经存在，则返回已存在的写入器
        
        Args:
            request_info (RequestInfo): 请求信息
            request_header (dict): 请求头
            
        Returns:
            ChunksWriter: 缓存文件的写入器
        """
        file_id = request_info.file_info.id
        sub_key = 'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
        
        task = await self.task_manager.get_task(ChunksWriter, file_id, sub_key)
        if task is not None:
            return task
        else:
            writer = ChunksWriter(request_info)
            await self.task_manager.create_task(ChunksWriter, file_id, writer, sub_key, ttl=40)

            return writer
    
    async def warm_up_tail_cache(self, request_info: RequestInfo, req_fs_header: dict):
        """
        预热尾部缓存
        
        Args:
            request_info (RequestInfo): 请求信息
            request_header (dict): 请求头
        """
        logger.debug("Warming up tail cache")
        # 定义缓存参数
        tail_request_info = copy.deepcopy(request_info)
        tail_request_info.cache_range_status = CacheRangeStatus.FULLY_CACHED_TAIL
        tail_request_info.range_info.cache_range = (
            request_info.file_info.size - 1 - INITIAL_CACHE_SIZE_OF_TAIL, 
            request_info.file_info.size - 1
            )
        
        tail_request_info.range_info.request_range = None
        tail_request_info.range_info.response_range = None
        
        sub_key = 'tail'
        file_id = tail_request_info.file_info.id
        
        # 防止异步中的竞争条件
        task = await self.task_manager.get_task(ChunksWriter, file_id, sub_key)
        if task is not None:
            logger.debug("Warm up tail cache task already exists, skipping")
            return
        
        writer = ChunksWriter(tail_request_info)
        await self.task_manager.create_task(ChunksWriter, file_id, writer, sub_key, ttl=40)
        await writer.write(await tail_request_info.raw_link_manager.get_raw_url(), req_fs_header)
        if not MEMORY_CACHE_ONLY:
            # 缓存写入硬盘
            disk_writer_key = f"disk_writer_{file_id}_{sub_key}"

            # 检查是否已经有写入任务
            if await self.task_manager.get_task(object, disk_writer_key) is None:
                # 创建一个虚拟任务来标记写入操作的开始
                await self.task_manager.create_task(object, disk_writer_key, object(), sub_key, ttl=60)
                asyncio.create_task(
                    self.storage.write_to_disk(
                        writer=writer,
                        file_info=tail_request_info.file_info,
                        range_info=tail_request_info.range_info,
                        item_info=tail_request_info.item_info
                    )
                )
            
    def verify_cache_file(self, file_info: FileInfo, start: int, end: int) -> bool:
        """
        验证缓存文件是否符合 Emby 文件大小，筛选出错误缓存文件
        
        实现方式仅为验证文件大小，不验证文件内容
        
        :param file_info: 文件信息
        :param cache_file_range: 缓存文件的起始点和结束点
        
        :return: 缓存文件是否符合视频文件大小
        """
        # 开头缓存文件
        if start == 0 and end == file_info.cache_file_size - 1:
            return True
        # 末尾缓存文件
        elif end == file_info.size - 1:
            return True
        else:
            return False
    
    async def start_write_cache_file(
        self,
        request_info: RequestInfo,
        cache_next_episode_tag: bool = False
    ):
        """
        开始写入缓存文件
        
        Args:
            request_info (RequestInfo): 请求信息
            cache_next_episode_tag (bool): 当前缓存任务是否为剧集的下一集缓存任务,防止递归缓存
        Returns:
            None
        """
        writer: ChunksWriter = await self.get_writer(request_info)
        
        url: str = await request_info.raw_link_manager.get_raw_url()
        # 构建request header
        req_fs_header = {
            'User-Agent': request_info.raw_link_manager.ua,
            'Host': url.split('/')[2],
        }
        
        await writer.write(url, req_fs_header)
        
        # 预热尾部缓存
        if await self.task_manager.get_task(ChunksWriter, request_info.file_info.id, 'tail') is None:
            # 预热尾部缓存
            await self.warm_up_tail_cache(request_info, req_fs_header)
        
        if not MEMORY_CACHE_ONLY:
            # 缓存写入硬盘
            file_id = request_info.file_info.id
            sub_key = 'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
            disk_writer_key = f"disk_writer_{file_id}_{sub_key}"

            # 检查是否已经有写入任务
            if await self.task_manager.get_task(object, disk_writer_key) is None:
                # 创建一个虚拟任务来标记写入操作的开始
                await self.task_manager.create_task(object, disk_writer_key, object(), sub_key, ttl=120)
                asyncio.create_task(
                    self.storage.write_to_disk(
                        writer=writer,
                        file_info=request_info.file_info,
                        range_info=request_info.range_info,
                        item_info=request_info.item_info
                    )
                )
                
        if not cache_next_episode_tag:
            asyncio.create_task(
                self.cache_next_episode(request_info)
            )     
        
    async def get_cache_file(
        self,
        request_info: RequestInfo
    ) -> AsyncGenerator[bytes, None]:
        
        # 检查内存缓存
        writer: Optional[ChunksWriter] = await self.task_manager.get_task(
            ChunksWriter, 
            request_info.file_info.id, 
            'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
        )
        rs, re = request_info.range_info.request_range
        logger.debug(f"Getting cache file for {request_info.file_info.name} from {rs} to {re}")
        if writer is not None:
            logger.debug(f"Cache file found in memory for {request_info.file_info.name}")
            return writer.read(rs, re)
        
        # 检查磁盘缓存
        if await self.storage.is_cached(
            file_info=request_info.file_info,
            range_info=request_info.range_info
        ):
            logger.debug(f"Cache file found on disk for {request_info.file_info.name}")
            return await self.storage.read_from_disk(
                file_info=request_info.file_info,
                range_info=request_info.range_info
            )
        
        logger.warning("在缓存不存在的时候尝试读取")
        return None
    
    async def get_cache_status(
        self,
        request_info: RequestInfo
    ) -> bool:
        """
        获取缓存状态（包含内存和磁盘缓存）
        
        Args:
            request_info (RequestInfo): 请求信息
            
        Returns:
            bool: 是否已缓存
        """
        return (await self.task_manager.get_task(
            ChunksWriter, 
            request_info.file_info.id, 
            'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
        ) is not None) or (await self.storage.is_cached(
            file_info=request_info.file_info,
            range_info=request_info.range_info
        ))

    async def cache_next_episode(self, request_info: RequestInfo):
        """
        针对剧集：缓存下一集；电影则跳过.
        应当作为Task放置在主异步循环中
        
        Args:
            request_info (RequestInfo): 请求信息
            
        """
        previous_item_info = request_info.item_info
        
        next_item_info = await get_next_episode_item_info(previous_item_info, request_info.api_key)
        if next_item_info is None:
            logger.debug(f"No next episode found for {previous_item_info.item_id}")
            return
        
        logger.debug(f"Next episode found: {next_item_info.item_id}")
        
        next_raw_link_manager = RawLinkManager(
            path=next_item_info.file_info.path,
            is_strm=next_item_info.file_info.is_strm,
            user_agent=request_info.user_agent or 'EmbyToAlist',
        )
        
        next_request_info = RequestInfo(
            file_info=next_item_info.file_info,
            range_info=next_item_info.range_info,
            raw_link_manager=next_raw_link_manager,
            item_info=next_item_info,
            api_key=request_info.api_key,
            cache_range_status=CacheRangeStatus.PARTIALLY_CACHED
        )
        
        # 检查是否已经缓存
        if await self.get_cache_status(next_request_info):
            logger.debug(f"Next episode {next_item_info.item_id} is already cached.")
            return
        
        # 开始缓存下一集
        await self.start_write_cache_file(next_request_info, cache_next_episode_tag=True)
