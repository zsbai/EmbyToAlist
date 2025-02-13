import asyncio
import hashlib
from pathlib import Path
from weakref import WeakValueDictionary
from contextlib import AbstractAsyncContextManager

import aiofiles
from loguru import logger

from ..models import FileInfo, RequestInfo, CacheRangeStatus
from typing import AsyncGenerator, Optional

class CacheWriter(AbstractAsyncContextManager):
    def __init__(self, file_path: str, lock, cache_key: str):
        self.file_path = file_path
        self.queue = asyncio.Queue()
        self.lock = lock
        self.cache_key = cache_key
        self._writer_task = None
        self._closed = False
    
    async def __aenter__(self):
        await self.precheck()
        # 启动后台写入任务
        self._writer_task = asyncio.create_task(self._writer())
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 关闭写入任务
        await self.close()
    
    async def precheck(self):
        """
        预检查缓存文件是否存在，如果存在则跳过写入
        """
        async with self.lock:
            if self.file_path.exists():
                logger.warning(f"Cache file {self.file_path} already exists, Skipping.")
                # 阻止后续的写入
                self._closed = True
            
            # 检查是否有重叠的缓存文件
            for cache_file in self.file_path.parent.iterdir():
                if cache_file.is_file():
                    if cache_file.name.startswith("cache_file"):
                        new_start, new_end = map(int, self.file_path.stem.split("_")[2:4])
                        old_start, old_end = map(int, cache_file.stem.split("_")[2:4])
                        if new_start >= old_start and new_end <= old_end:
                            logger.warning(f"Overlapping cache file found: {cache_file}")
                            self._closed = True
                            break
                        if new_start <= old_start and new_end >= old_end:
                            logger.warning(f"Existing Cache Range within new range. Deleting old cache.")
                            cache_file.unlink()
                             
    async def _writer(self):
        """
        后台任务：持续从队列中读取数据块，顺序写入文件。
        当接收到特殊标记（例如 None）时，退出任务。
        """
        async with self.lock:
            if self.file_path.exists():
                logger.warning(f"Cache file {self.file_path} already exists, Skipping.")
                # 阻止后续的写入
                self._closed = True
                # 丢弃队列中的所有数据
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                        self.queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                return
            
            async with aiofiles.open(self.file_path, mode="wb") as f:
                while True:
                    chunk = await self.queue.get()
                    if chunk is None:
                        # 收到退出信号
                        self.queue.task_done()
                        break
                    await f.write(chunk)
                    self.queue.task_done()
            
            # 写入完成后，不再接受新的数据块
            self._closed = True
    
    async def write(self, chunk: bytes):
        """
        异步投递数据块到写入队列。
        多个任务可并发调用该方法。
        """
        if self._closed:
            # 避免重复写入
            logger.warning(f"Cache writer for {self.file_path} is closed, skipping write.")
            return
        await self.queue.put(chunk)
    
    async def close(self):
        """
        关闭缓存写入器：
        1. 等待队列中所有任务完成。
        2. 投递退出信号（None）并等待后台任务退出。
        """
        if self._closed:
            return
        await self.queue.join()  # 等待所有任务完成
        await self.queue.put(None)  # 投递结束信号
        if self._writer_task is not None:
            await self._writer_task
        self._closed = True
        

class CacheSystem():
    VERSION: str = "1.0.0"
    def __init__(self, root_dir: str):
        self.root_dir: Path = Path(root_dir)
        self.cache_locks = WeakValueDictionary()
        self._initialize()
        
    def _write_version_file(self):
        """Write the version file to the cache directory.
        """
        version_file = self.root_dir / ".version"
        with version_file.open("w") as f:
            f.write(self.VERSION)
            
    def _read_version_file(self):
        """Read the version file from the cache directory.
        """
        version_file = self.root_dir / ".version"
        if not version_file.exists():
            return None
        return version_file.read_text().strip()
    
    def _get_cache_lock(self, subdirname: Path, dirname: Path):
        # 为每个子目录创建一个锁, 防止不同文件名称的缓存同时写入，导致重复范围的文件
        key = f"{subdirname}/{dirname}" 
        if key not in self.cache_locks:
            # 防止被weakref立即回收
            lock = asyncio.Lock()
            self.cache_locks[key] = lock
        return self.cache_locks[key]
    
    def _get_hash_subdirectory_from_path(self, file_info: FileInfo) -> tuple[str, str]:
        """
        计算给定文件路径的MD5哈希，并返回哈希值的前两位作为子目录名称 (Cache Key)。
        缓存键为文件名称+文件大小+文件类型

        :param file_info: 文件信息
        
        :return: 哈希值的前两个字符，作为子目录名称
        """
        cache_key = f"{file_info.name}:{file_info.size}:{file_info.container}"
        hash_digest = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
        return hash_digest[:2], hash_digest # 返回子目录名称和哈希值
            
    def _initialize(self):
        """初始化缓存系统
        """
        if not self.root_dir.exists():
            self.root_dir.mkdir(parents=True, exist_ok=True)
            self._write_version_file()
        else:
            version = self._read_version_file()
            if version != self.VERSION:
                logger.warning(f"Cache version mismatch, current version: {self.VERSION}, cache version: {version}")
                logger.warning("Please clear the cache directory")
                exit(1)
                
    def get_writer(self, request_info: RequestInfo) -> CacheWriter:
        """创建缓存写入器
        """
        file_info = request_info.file_info
        
        subdirname, dirname = self._get_hash_subdirectory_from_path(file_info)        
        start_point, end_point = request_info.range_info.cache_range
        
        cache_file_name = f'cache_file_{start_point}_{end_point}'
        cache_file_dir = self.root_dir / subdirname / dirname
        cache_file_path = cache_file_dir / cache_file_name
        
        cache_file_dir.mkdir(parents=True, exist_ok=True)
        
        writer = CacheWriter(cache_file_path, self._get_cache_lock(subdirname, dirname), self._get_hash_subdirectory_from_path(file_info)[1])
        
        return writer
    
    def verify_cache_file(file_info: FileInfo, cache_file_range: tuple[int, int]) -> bool:
        """
        验证缓存文件是否符合 Emby 文件大小，筛选出错误缓存文件
        
        实现方式仅为验证文件大小，不验证文件内容
        
        :param file_info: 文件信息
        :param cache_file_range: 缓存文件的起始点和结束点
        
        :return: 缓存文件是否符合视频文件大小
        """
        start, end = cache_file_range
        # 开头缓存文件
        if start == 0 and end == file_info.cache_file_size - 1:
            return True
        # 末尾缓存文件
        elif end == file_info.size - 1:
            return True
        else:
            return False
    
    def get_cache_status(self, request_info: RequestInfo) -> bool:
        """检查缓存状态
        """
        file_info = request_info.file_info
        
        subdirname, dirname = self._get_hash_subdirectory_from_path(file_info)
        cache_dir = self.root_dir / subdirname / dirname
        
        if not cache_dir.exists():
            return False
        
        for cache_file in cache_dir.iterdir():
            if cache_file.is_file():
                if cache_file.name.startswith("cache_file"):
                    start, end = map(int, cache_file.stem.split("_")[2:4])
                    if self.verify_cache_file(file_info, (start, end)):
                        if start <= request_info.start_byte <= end:
                            return True
                    else:
                        logger.warning(f"Invalid cache file: {cache_file}")
                        cache_file.unlink()
                        return False
        
        logger.debug(f"No valid cache file found for {file_info.path}")
        return False
    
    def read_cache_file(self, request_info: RequestInfo) -> AsyncGenerator[bytes, None]:
        """
        读取缓存文件，该函数不是异步的，将直接返回一个异步生成器
        
        :param request_info: 请求信息
        
        :return: function read_file
        """    
        subdirname, dirname = self._get_hash_subdirectory_from_path(request_info.file_info)
        file_dir = self.root_dir / subdirname / dirname
        range_info = request_info.range_info
        
        # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
        for cache_file in file_dir.iterdir():
            if cache_file.is_file():
                if cache_file.name.startswith("cache_file"):
                    start, end = map(int, cache_file.stem.split("_")[2:4])
                    if start <= range_info.request_range[0] <= end:
                        # 调整 end_point 的值
                        adjusted_end = None if request_info.cache_range_status in {CacheRangeStatus.PARTIALLY_CACHED, CacheRangeStatus.FULLY_CACHED_TAIL} else range_info.request_range[1] - range_info.request_range[0]
                        logger.debug(f"Read Cache: {cache_file}")
                        
                        return self.read_file(cache_file, range_info.request_range[0] - start, adjusted_end)
                
        logger.error(f"Read Cache Error: There is no matched cache in the cache directory for this file: {request_info.file_info.path}.")
        return 
    
    async def read_file(
        self,
        file_path: str, 
        start_point: int = 0, 
        end_point: Optional[int] = None, 
        chunk_size: int = 1024*1024, 
        ) -> AsyncGenerator[bytes, None]:
        """
        读取文件的指定范围，并返回异步生成器。
    
        :param file_path: 缓存文件路径
        :param start_point: 文件读取起始点，HTTP Range 的字节范围
        :param end_point: 文件读取结束点，None 表示文件末尾，HTTP Range 的字节范围
        :param chunk_size: 每次读取的字节数，默认为 1MB
        
        :return: 生成器，每次返回 chunk_size 大小的数据
        """
        try:
            async with aiofiles.open(file_path, 'rb') as f:
                await f.seek(start_point)
                while True:
                    if end_point is not None:
                        # 传入的range为http请求头的range，直接传入默认会少读取1个字节，所以需要+1
                        remaining = (end_point+1) - await f.tell()
                        if remaining <= 0:
                            break
                        chunk_size = min(chunk_size, remaining)
                    
                    data = await f.read(chunk_size)
                    if not data:
                        break
                    yield data
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
        except Exception as e:
            logger.error(f"Unexpected error occurred while reading file: {e}")
    
    async def write_from_remote(self):
        pass
    
