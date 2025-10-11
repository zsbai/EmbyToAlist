import asyncio
import time

import httpx
from fastapi import HTTPException
from loguru import logger

from ..config import CHUNK_SIZE_OF_CHUNKSWITER
from ..utils.common import ClientManager
from ..models import RequestInfo, CacheRangeStatus, FileHeaders
from typing import AsyncGenerator, Optional


class ChunksWriter():
    def __init__(
        self, 
        request_info: RequestInfo, 
    ):
        
        self.client: httpx.AsyncClient = ClientManager.get_client()
        
        self.queue = asyncio.Queue()
        self.cache_data = bytearray()
        
        self.task: asyncio.Task = None
                
        self.condition = asyncio.Condition()
        self.completed: bool = False
        self.error: Optional[Exception] = None
        
        # initialize cache data
        self.cache_range_end: int = request_info.range_info.cache_range[1]
        self.cache_range_start: int = request_info.range_info.cache_range[0]
        self.cache_range_status: CacheRangeStatus = request_info.cache_range_status
        self.request_header: dict = None
        
        # 针对末尾缓存的情况
        # 播放器通常在请求末尾时适当的增加请求范围
        # 所以我们需要缓存最大的请求范围
        # 例如第一次请求范围为 70000-80000
        # 第二次请求范围为 60000-80000
        # 则我们需要缓存 60000-80000
        self.smallest_request_start_point: int = float("inf")
        
        # 存储从后端响应中提取的文件头信息
        self.file_headers: Optional[FileHeaders] = None

    def __del__(self):
        logger.debug(f"ChunksWriter: {self.cache_range_start}-{self.cache_range_end} has been deleted")
        
    async def _write(self, raw_url: str, req_fs_header: dict):
        """异步写入缓存文件
        
        :param raw_url: 直链URL
        """
        self.request_header = req_fs_header
        
        # 每个chunk 2MB
        chunk_size = CHUNK_SIZE_OF_CHUNKSWITER
        
        if self.cache_range_start == 0:
            # 读取头部
            self.request_header['Range'] = f"bytes=0-{self.cache_range_end}"
        else:
            # 读取尾部
            self.request_header['Range'] = f"bytes={self.cache_range_start}-{self.cache_range_end}"

        logger.debug(f"Header of File Source Request: {self.request_header}")

        before = time.time()
        try:
            async with self.client.stream(
                "GET",
                raw_url,
                headers=self.request_header,
                timeout=httpx.Timeout(30)
            ) as response:
                
                if response.status_code != 206:
                    raise ValueError(f"Expected 206 response, got {response.status_code}")
                
                # 提取文件头信息
                logger.debug(f"File Source Response Headers: {response.headers}")
                etag = response.headers.get('ETag')
                last_modified = response.headers.get('Last-Modified')
                content_disposition = response.headers.get('Content-Disposition')
                
                # 只有当至少有一个头信息存在时才创建FileHeaders对象
                if etag or last_modified or content_disposition:
                    self.file_headers = FileHeaders(
                        etag=etag,
                        last_modified=last_modified,
                        content_disposition=content_disposition
                    )
                    logger.debug(f"Extracted headers: ETag={self.file_headers.etag}, Last-Modified={self.file_headers.last_modified}")
                else:
                    self.file_headers = None
                    logger.debug("No headers found in response")
                
                logger.debug(f"======== Cache write started for range {self.cache_range_start}-{self.cache_range_end} =======")
                
                async for chunk in response.aiter_bytes(chunk_size):
                    # 写入缓存文件
                    async with self.condition:
                        self.cache_data.extend(chunk)
                        self.condition.notify_all() 
                
                async with self.condition:
                    logger.debug(f"======== Cache write completed for range {self.cache_range_start}-{self.cache_range_end} in <{time.time() - before:.2f}> seconds =======")
                    self.completed = True
                    self.condition.notify_all()
        except Exception as e:
            logger.error(f"Error occurred during chunk writing: {repr(e)}")
            async with self.condition:
                self.error = e
                self.completed = True
                self.condition.notify_all()
        
    async def write(self, raw_url: str, req_fs_header: dict):
        """创建写入异步任务
        
        :param raw_url: 直链URL
        """
        if self.task is None:
            self.task = asyncio.create_task(self._write(raw_url, req_fs_header))
        else:
            logger.debug("Write task already exists, skipping")
            return
            
    async def read(self, start: Optional[int] = None, end: Optional[int] = None) -> AsyncGenerator[bytes, None]:
        """读取缓存文件，start 和 end 留空表示读取全部
        
        如果cache data不为空，则直接返回
        否则从队列中读取数据，并将数据写入缓存文件

        1. 如果请求的范围内数据已缓存，则直接返回对应数据
        2. 如果请求结束位置尚未缓存且缓存还未完成，则等待数据到达；
        3. 如果请求的结束位置超出目标缓存，则在缓存写入完成后返回实际可用数据。

        Args:
            start (Optional[int]): 请求开始字节, 如果不传入则表示从头读取
            end (Optional[int], optional): 请求结尾字节，None表示最后. Defaults to None.
        Yields:
            AsyncGenerator[bytes, None]: 文件异步生成器
        """
        if start is None:
            start = 0
        
        # 当 end 为 None 时，设置为无限大
        if end is None:
            end = float("inf")
        else:
            end = end + 1
        
        # 针对请求末尾的情况
        if self.cache_range_status == CacheRangeStatus.FULLY_CACHED_TAIL:
            logger.debug(f"smallest_request_start_point: {self.smallest_request_start_point}, start: {start}, end: {end}")
            if start < self.smallest_request_start_point:
                self.smallest_request_start_point = start
            
            # 修正 start 和 end 范围
            # 假设cache range : 70000-80000
            # start: 75000, end: 80000   
            # 修正为 start: 5000, end: 10000
            if start >= self.cache_range_start:
                start = start - self.cache_range_start
                # 请求末尾时，一般end都为None，默认无限大无须修改
                # end = end - self.cache_range_start
            else:
                logger.error(f"Invalid start point: {start}, cache range start: {self.cache_range_start}")
                logger.error(f"请尝试提高末尾缓存的阈值")
                raise HTTPException(status_code=500, detail="Invalid start point")

        current_index = start
        while current_index < end:
            async with self.condition:
                await self.condition.wait_for(lambda: len(self.cache_data) > current_index or self.completed)
                
                if self.error is not None:
                    raise IOError(f"Error occurred during cache writing: {repr(self.error)}")
                
                new_end = min(end, len(self.cache_data))
                
            if new_end > current_index:
                yield bytes(self.cache_data[current_index:new_end])
                current_index = new_end
            
            # 如果写入已完成且没有更多数据，则退出循环
            if self.completed and current_index >= len(self.cache_data):
                break
