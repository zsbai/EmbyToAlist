import asyncio
from pathlib import Path
from copy import deepcopy

from loguru import logger

from ..config import INITIAL_CACHE_SIZE_OF_TAIL, MEMORY_CACHE_ONLY
from ..models import FileInfo, RequestInfo, CacheRangeStatus, ItemInfo
from .manager import AppContext
from ..utils.common import ClientManager
from ..utils.path import check_file_path
from ..cache.writer import ChunksWriter
from ..cache.storage.file_storage import FileStorage
from ..providers.media_server.emby.items import get_next_episode_item_info, get_file_info
from ..providers.media_server.emby.client import EmbyClient
from ..providers.manager import RawLinkManager
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
    
    async def _get_writer(self, request_info: RequestInfo) -> ChunksWriter:
        """
        获取缓存文件的写入器, 如果缓存文件已经存在，则返回已存在的写入器
        
        ChunkWriter 存活时间为 180 秒，超过时间则会被删除
        
        Args:
            request_info (RequestInfo): 请求信息
            request_header (dict): 请求头
            
        Returns:
            ChunksWriter: 缓存文件的写入器
        """
        file_id = request_info.file_info.id
        sub_key = 'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
        
        task: ChunksWriter = await self.task_manager.get_task(ChunksWriter, file_id, sub_key)
        if task is not None:
            return task
        else:
            writer = ChunksWriter(request_info)
            await self.task_manager.create_task(ChunksWriter, file_id, writer, sub_key, ttl=180)

            return writer
        
    async def _write_to_disk(self, request_info: RequestInfo, writer: ChunksWriter):
        """
        将内存中的缓存写入到磁盘中
        
        Args:
            request_info (RequestInfo): 请求信息
            writer (ChunksWriter): 缓存写入器
        """
        sub_key = 'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
        disk_writer_key = f"disk_writer_{request_info.file_info.id}_{sub_key}"

        if await self.task_manager.get_task(object, disk_writer_key, sub_key) is None:
            try:
                # 创建一个虚拟任务来标记写入操作的开始
                # 不设置过期时间，写入完成后手动删除
                await self.task_manager.create_task(object, disk_writer_key, object(), sub_key, ttl=None)
                
                # 避免IO阻塞
                await asyncio.sleep(60)

                if not writer.completed:
                    for _ in range(3):
                        if writer.completed:
                            break
                        await asyncio.sleep(10)

                    logger.warning(f"writer not completed after waiting; aborting disk write for safety.(key={disk_writer_key})")
                    return
                
                try:
                    await asyncio.wait_for(
                            self.storage.write_to_disk(
                                writer=writer,
                                file_info=request_info.file_info,
                                range_info=request_info.range_info,
                                item_info=request_info.item_info
                            ), timeout=90 # 这里的90秒 = 180[ChunkWriter存活时间] - (60+3*10)[最大等待时间]
                    )
                    
                except asyncio.TimeoutError:
                    logger.error(f"Disk write operation timed out (key={disk_writer_key})")
                except Exception as e:
                    logger.error(f"Error during disk write operation (key={disk_writer_key}): {repr(e)}")
            finally:
                # 兜底，保证任务被移除
                logger.warning(f"Removing disk write task (key={disk_writer_key})")
                await self.task_manager.remove_task(object, disk_writer_key, sub_key)
    
    async def _warm_up_tail_cache(self, request_info: RequestInfo, req_fs_header: dict):
        """
        针对传入的request_info，预热当前资源的末尾缓存
        
        Args:
            request_info (RequestInfo): 请求信息
            request_header (dict): 请求头
        """
        logger.debug("Warming up tail cache")
        # 定义缓存参数
        tail_request_info = deepcopy(request_info)
        tail_request_info.cache_range_status = CacheRangeStatus.FULLY_CACHED_TAIL
        tail_request_info.range_info.cache_range = (
            request_info.file_info.size - 1 - INITIAL_CACHE_SIZE_OF_TAIL, 
            request_info.file_info.size - 1
            )
        
        tail_request_info.range_info.request_range = None
        tail_request_info.range_info.response_range = None
        
        writer = await self._get_writer(tail_request_info)
        
        await writer.write(await tail_request_info.raw_link_manager.get_raw_url(), req_fs_header)
        if not MEMORY_CACHE_ONLY:
            # 缓存写入硬盘
            asyncio.create_task(
                self._write_to_disk(tail_request_info, writer)
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
    ) -> ChunksWriter:
        """
        如果已经有对应的chunk writer，则直接返回；
        
        开始向内存写入缓存文件，可以直接通过 ChunksWriter 中的read方法，读取缓存文件的任意部分
        
        Args:
            request_info (RequestInfo): 请求信息
            cache_next_episode_tag (bool): 当前缓存任务是否为剧集的下一集缓存任务,防止递归缓存
        Returns:
            ChunksWriter: 缓存文件的写入器
        """
        writer: ChunksWriter = await self._get_writer(request_info)
        
        if writer.task is not None:
            # 已经有写入任务，直接返回
            return writer
        
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
            await self._warm_up_tail_cache(request_info, req_fs_header)
        
        if not MEMORY_CACHE_ONLY:
            # 缓存写入硬盘
            asyncio.create_task(
                self._write_to_disk(request_info, writer)
            )
                
        if not cache_next_episode_tag:
            # 只在请求开头时尝试缓存下一集
            if request_info.cache_range_status != CacheRangeStatus.FULLY_CACHED_TAIL:
                asyncio.create_task(
                    self.cache_next_episode(request_info)
                )
        
        return writer
        
    async def get_cache_file(
        self,
        request_info: RequestInfo,
        start: Optional[int] = None,
        end: Optional[int] = None
    ) -> AsyncGenerator[bytes, None]:
        
        """
        获取缓存文件的内容, 会优先从内存中获取缓存文件，如果内存中没有，则从磁盘中获取
        
        不传入 start 和 end 时，返回range_info 中的 request_range 范围

        Args:
            request_info (RequestInfo): 请求信息
            start (Optional[int]): 起始位置
            end (Optional[int]): 结束位置

        Returns:
            AsyncGenerator[bytes, None]: 缓存文件的内容生成器
        """
        
        # 检查内存缓存
        writer: Optional[ChunksWriter] = await self.task_manager.get_task(
            ChunksWriter, 
            request_info.file_info.id, 
            'tail' if request_info.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else 'head'
        )
        if start is None and end is None:
            start, end = request_info.range_info.request_range
            
        logger.debug(f"Getting cache file for {request_info.file_info.name} from {start} to {end}")
        if writer is not None:
            logger.debug(f"Cache file found in memory for {request_info.file_info.name}")
            return writer.read(start, end)

        # 检查磁盘缓存
        # TODO: 目前磁盘的后端range仍然默认从range_info.request_range获取
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
        # 防止产生过多的并发请求
        await asyncio.sleep(100)
        
        previous_item_info = request_info.item_info
        next_item_info: ItemInfo = await get_next_episode_item_info(
            previous_item_info, 
            EmbyClient(api_key=request_info.api_key)
        )
        if next_item_info is None:
            logger.debug(f"Next episode not found for {previous_item_info.item_id}")
            return
        
        file_infos: list[FileInfo] = await get_file_info(
            EmbyClient(api_key=request_info.api_key),
            next_item_info.item_id,
            media_source_id=None
        )
                
        for file_info in file_infos:
            logger.info(f"Next episode found: {file_info.name}(item_id: {next_item_info.item_id}, file_id: {file_info.id}), starting caching...")
            
            result = check_file_path(file_info)
            if not result.valid:
                logger.debug(f"Next episode {next_item_info.item_id} path {file_info.path} is not valid for Alist, skipping")
                continue
            else:
                file_info = result.transformed_file_info
                        
            next_request_info = deepcopy(request_info)
            next_request_info.file_info = file_info
            next_request_info.item_info = next_item_info
            next_request_info.raw_link_manager = None
            
            # 检查是否已经缓存
            if await self.get_cache_status(next_request_info):
                logger.debug(f"Next episode {next_item_info.item_id}(file_id: {file_info.id}) is already cached.")
                return
            else:
                # 创建 RawLinkManager
                next_raw_link_manager = RawLinkManager(
                    path=file_info.path,
                    is_strm=file_info.is_strm,
                    ua=request_info.user_agent or 'EmbyToAlist',
                )
                await next_raw_link_manager.create_task()
            
            # 开始缓存
            await self.start_write_cache_file(next_request_info, cache_next_episode_tag=True)
            logger.info(f"Started caching next episode {next_item_info.item_id}: {file_info.name}")
