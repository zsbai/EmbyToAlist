import asyncio
import hashlib
from pathlib import Path
from weakref import WeakValueDictionary

import aiofiles
import aiofiles.os
from loguru import logger

from ...models import RequestInfo, CacheRangeStatus, FileInfo, RangeInfo
from ...config import MEMORY_CACHE_ONLY
from ..writer import ChunksWriter
from ...utils.database import TinyDBHandler
from typing import Optional, AsyncGenerator, Callable

class FileStorage:
    def __init__(self, root_dir: str, version: str):
        self.root_dir = Path(root_dir)
        self.version = version
        self.cache_locks = WeakValueDictionary()
        self.db_lock = asyncio.Lock()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        
        self.db = None
        
        self._check_version()
        self._initialize_db()
    
    def _check_version(self):
        version_file = self.root_dir / ".version"
        if not version_file.exists():
            with version_file.open("w") as f:
                f.write(self.version)
        else:
            current_version = version_file.read_text().strip()
            if current_version != self.version:
                logger.error(f"Cache version mismatch, please clear the cache directory: {self.root_dir} before using.")
                raise RuntimeError(f"Cache version mismatch: expected {self.version}, found {current_version}")
            
    def _initialize_db(self):
        """
        初始化数据库
        """
        self.db = TinyDBHandler(self.root_dir / "data.json")
        self.db.set_table('system_info')
        
        # 检查系统信息表是否为空，如果为空则插入初始化信息
        if not self.db.get_all():
            info = {
                'version': self.version,
                'root_dir': str(self.root_dir),
                'cache_size': 0, # unit: bytes
                'cache_count': 0,
            }
            self.db.insert_one(info)
        else:
            # 如果非空，检查版本是否匹配
            version = self.db.get_all()[0].get('version', None)
            if version != self.version:
                logger.error(f"Cache version mismatch, please clear the cache directory: {self.root_dir} before using.")
                raise RuntimeError(f"Cache version mismatch: expected {self.version}, found {version}")

        # 初始化缓存文件表
        self.db.set_table('cache_files')
        
    # @deprecate         
    # def _get_hash_subdirectory_from_path(self, file_info: FileInfo) -> tuple[str, str]:
    #     """
    #     计算给定文件路径的MD5哈希，并返回哈希值的前两位作为子目录名称 (Cache Key)。
    #     缓存键为文件名称+文件大小+文件类型

    #     :param file_info: 文件信息
        
    #     :return: 哈希值的前两个字符，作为子目录名称
    #     """
    #     cache_key = f"{file_info.name}:{file_info.size}:{file_info.container}"
    #     hash_digest = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
    #     return hash_digest[:2], hash_digest # 返回子目录名称和哈希值
    
    def _hash_dir(self, file_info: FileInfo) -> Path:
        """
        根据固定key返回经过哈希处理的缓存目录, 没有则创建
        """
        key = f"{file_info.name}:{file_info.size}:{file_info.container}"
        hash_digest = hashlib.md5(key.encode('utf-8')).hexdigest()
        cache_dir: Path = self.root_dir / hash_digest[:2] / hash_digest
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
    
    def _get_cache_lock(self, cache_dir: Path):
        # 为每个子目录创建一个锁, 防止不同文件名称的缓存同时写入，导致重复范围的文件
        key = cache_dir.__str__()
        if key not in self.cache_locks:
            # 防止被weakref立即回收
            lock = asyncio.Lock()
            self.cache_locks[key] = lock
        return self.cache_locks[key]
    
    async def _is_already_fully_cached(
        self,
        cache_dir: Path,
        start: int,
        end: int
    ) -> bool:
        """
        检查是否已有一个文件，其范围“完全包含” [start, end]; 
        
        Args:
            cache_dir (Path): 缓存目录
            start (int): 起始点
            end (int): 结束点
        Returns:
            bool: 是否已经缓存
        """
        pattern = "cache_file_*_*"
        for file in cache_dir.glob(pattern):
            if file.is_file() and file.suffix != ".tmp":
                s, e = map(int, file.stem.split("_")[2:4])
                if s <= start and end <= e:
                    return True
        return False
    
    async def _cleanup_overlap_files(
        self, 
        cache_dir: Path, 
        start: int, 
        end: int
        ):
        """
        删除那些被 [start, end] 完全包含的旧缓存文件
        
        Args:
            cache_dir (Path): 缓存目录
            start (int): 起始点
            end (int): 结束点
        """
        pattern = "cache_file_*_*"
        for file in cache_dir.glob(pattern):
            if file.is_file() and file.suffix != ".tmp":
                s, e = map(int, file.stem.split("_")[2:4])
                if start <= s and e <= end:
                    logger.debug(f"Removing overlapping cache file: {file}")
                    await aiofiles.os.remove(str(file))
    
    async def _stream_file(
        self,
        file_path: Path,
        start_point: int,
        end_point: Optional[int],
        chunk_size: int = 1024 * 1024
    ) -> AsyncGenerator[bytes, None]:
        """
        读取文件的指定范围，并返回异步生成器。
                
        Args:
            file_path (str): 缓存文件路径
            start_point (int): 文件读取起始点，HTTP Range 的字节范围
            end_point (int): 文件读取结束点，None 表示文件末尾，HTTP Range 的字节范围
            chunk_size (int): 每次读取的字节数，默认为 1MB
        
        Yields:
            bytes: 读取到的数据块
            
        Raises:
            FileNotFoundError: 如果文件不存在
            Exception: 其他异常
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

    async def get_cache_file_path(
        self,
        file_info: FileInfo,
        range_info: RangeInfo
    ) -> Optional[tuple[Path, int]]:
        """
        获取可用的缓存文件路径，和_is_already_fully_cached不同，当前函数仅检查start point
        
        Args:
            file_info (FileInfo): 文件信息
            range_info (RangeInfo): 范围信息
        Returns:
            Optional[tuple[Path, int]]: 返回缓存文件路径和起始点，如果没有找到则返回 None
        """
        
        cache_dir = self._hash_dir(file_info)
        rs, re = range_info.request_range
        
        if not cache_dir.exists():
            logger.debug(f"Cache directory does not exist: {cache_dir}")
            return False
        
        for f in cache_dir.iterdir():
            if f.is_file() and not f.name.endswith(".tmp"):
                s, e = map(int, f.stem.split("_")[2:4])
                if s <= rs <= e:
                    logger.debug(f"Cache file found: {f}")
                    # 更新最后读取时间
                    self.db.set_table('cache_files')
                    self.db.update(
                        fields={'last_read_time': f.stat().st_atime},
                        condition=lambda q: q.path == str(cache_dir)
                    )
                    return f, s
                
        logger.debug(f"No valid cache file found for {file_info.path}")
        return None
        
    async def write_to_disk(
        self,
        writer: ChunksWriter,
        file_info: FileInfo,
        range_info: RangeInfo
    ):
        """
        将内存缓存写入到磁盘
        
        Args:
            writer (ChunksWriter): 分块写入器
            file_info (FileInfo): 文件信息
            range_info (RangeInfo): 范围信息
        """
        # await asyncio.sleep(20)  # 延迟写入，防止阻塞
        
        # check writer is complete or not every 20 seconds
        for _ in range(3):
            if writer.completed:
                break
            await asyncio.sleep(20)
        
        cache_dir = self._hash_dir(file_info)
        start, end = range_info.cache_range
        
        # deprecated
        # start = writer.smallest_request_start_point if writer.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL else start
        
        fname = f"cache_file_{start}_{end}"
        temp_path = cache_dir / f"{fname}.tmp"
        
        async with self._get_cache_lock(cache_dir):
            if await self._is_already_fully_cached(cache_dir, start, end):
                logger.debug(f"Cache file already exists for range {start}-{end}, skipping write")
                return
            
            await self._cleanup_overlap_files(cache_dir, start, end)
            
            async with aiofiles.open(temp_path, 'wb') as f:
                async for chunk in writer.read(start, end):
                    await f.write(chunk)
            
            final_path = cache_dir / fname
            await aiofiles.os.rename(temp_path, final_path)
            logger.info(f"Cache file written: {final_path}")
            await self._update_cache_stats(final_path)
        
    async def _update_cache_stats(self, file_path: Path):
        """
        更新缓存统计信息
        """
        file_size = file_path.stat().st_size
        cache_dir = file_path.parent

        self.db.set_table('cache_files')
        # 检查缓存目录是否已存在
        is_new = not self.db.search(lambda q: q.path == str(cache_dir))
        
        if is_new:
            # 如果不存在，则插入新记录
            self.db.insert_one({
                'path': str(cache_dir),
                'size': file_size,
                'created_at': file_path.stat().st_ctime,
                'last_read_time': file_path.stat().st_atime,
                'score': 100  # 初始分数
            })
        else:
            # 如果存在，则只更新大小
            self.db.update(
                fields=lambda doc: {'size': doc.get('size', 0) + file_size},
                condition=lambda q: q.path == str(cache_dir)
            )

        # 更新全局统计
        async with self.db_lock:
            self.db.set_table('system_info')
            
            def update_fields(doc):
                fields = {'cache_size': doc.get('cache_size', 0) + file_size}
                if is_new:
                    fields['cache_count'] = doc.get('cache_count', 0) + 1
                return fields

            self.db.update(
                condition=lambda q: q.version == self.version,
                fields=update_fields
            )

    async def read_from_disk(
        self,
        file_info: FileInfo,
        range_info: RangeInfo
    ) -> AsyncGenerator[bytes, None]:
        """
        从磁盘读取缓存文件,该函数不是异步的，将直接返回一个异步生成器
        
        Args:
            file_info (FileInfo): 文件信息
            range_info (RangeInfo): 范围信息
        Returns:
            AsyncGenerator[bytes, None]: 异步生成器函数
        """
        rs, re = range_info.request_range
        
        cache_file, fs = await self.get_cache_file_path(file_info, range_info)

        # start 减去 缓存文件的起始点，修正读取位置
        return self._stream_file(cache_file, rs - fs, re)

    async def is_cached(
        self,
        file_info: FileInfo,
        range_info: RangeInfo
    ) -> bool:
        """
        检查是否已缓存
        
        Args:
            file_info (FileInfo): 文件信息
            range_info (RangeInfo): 范围信息
        Returns:
            bool: 是否已缓存
        """
        return await self.get_cache_file_path(file_info, range_info) is not None