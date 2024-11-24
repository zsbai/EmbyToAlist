import asyncio
import os
from weakref import WeakValueDictionary

import aiofiles
import aiofiles.os
import httpx
from uvicorn.server import logger

from components.utils import *
from main import get_or_cache_alist_raw_url, FileInfo, RequestInfo, CacheStatus
from typing import AsyncGenerator, Optional

cache_locks = WeakValueDictionary()

def get_cache_lock(subdirname, dirname):
    # 为每个子目录创建一个锁, 防止不同文件名称的缓存同时写入，导致重复范围的文件
    key = os.path.join(subdirname, dirname)  
    if key not in cache_locks:
        # 防止被weakref立即回收
        lock = asyncio.Lock()
        cache_locks[key] = lock
    return cache_locks[key]

async def read_file(
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
        
async def write_cache_file(item_id, request_info: RequestInfo, req_header=None, client: httpx.AsyncClient=None) -> bool:
    """
    写入缓存文件，end point通过cache_size计算得出
    
    缓存文件名格式为 cache_file_{start_point}_{end_point}，start 和 end 都为 HTTP Range 的字节范围（即需-1）
    
    :param item_id: Emby Item ID
    :param request_info: 请求信息
    :param req_header: 请求头，用于请求Alist Raw Url
    :param client: HTTPX异步客户端
    
    :return: 缓存是否成功
    """    
    path = request_info.file_info.path
    file_size = request_info.file_info.size
    cache_size = request_info.file_info.cache_file_size
    
    subdirname, dirname = get_hash_subdirectory_from_path(path, request_info.item_info.item_type)
    
    # 计算缓存文件的结束点
    # 如果 start_point 大于 cache_size，endPoint 为文件末尾（将缓存尾部元数据）
    if request_info.cache_status in {CacheStatus.PARTIAL, CacheStatus.HIT}:
        start_point = 0
        end_point = cache_size - 1
    elif request_info.cache_status == CacheStatus.HIT_TAIL:
        start_point = request_info.start_byte
        end_point = file_size - 1
    else:
        logger.error(f"Cache Error {request_info.start_byte}, File Size is None")
        return False
    
    # 获取Alist Raw Url
    if request_info.raw_url is None:
        raw_url = await request_info.raw_url_task
    else:
        raw_url = request_info.raw_url
    
    # 根据起始点和缓存大小确定缓存文件路径
    cache_file_name = f'cache_file_{start_point}_{end_point}'
    cache_file_path = os.path.join(cache_path, subdirname, dirname, cache_file_name)
    logger.debug(f"Start to cache file {start_point}-{end_point}: {item_id}, file path: {cache_file_path}")
    
    os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)
     
    cache_write_tag_path = os.path.join(cache_path, subdirname, dirname, f'{cache_file_name}.tag')
    lock = get_cache_lock(subdirname, dirname)
    
    async with lock:
        # 创建缓存写入标记文件
        async with aiofiles.open(cache_write_tag_path, 'w') as f:
            pass
    
        # 检查是否已有包含当前范围的缓存文件
        for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
            if file.startswith('cache_file_') and file.endswith('.tag') is False:
                file_range_start, file_range_end = map(int, file.split('_')[2:4])
                
                if start_point >= file_range_start and end_point <= file_range_end:
                    logger.warning(f"Cache Range Already Exists. Abort.")
                    await aiofiles.os.remove(cache_write_tag_path)
                    return False
                elif start_point <= file_range_start and end_point >= file_range_end:
                    logger.warning(f"Existing Cache Range within new range. Deleting old cache.")
                    await aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
        
        # 请求Alist Raw Url，好像请求头没太所谓
        if req_header is None:
            req_header = {}
        else:
            req_header = dict(req_header) # Copy the headers
            
        req_header['host'] = raw_url.split('/')[2]
        # Modify the range to startPoint-first50M
        req_header['range'] = f"bytes={start_point}-{end_point}"

        try:
            # 请求数据
            resp = await client.get(raw_url, headers=req_header)
            if resp.status_code != 206:
                logger.error(f"Write Cache Error {start_point}-{end_point}: Upstream return code: {resp.status_code}")
                raise ValueError("Upstream response code not 206")
            
            # 写入缓存文件
            async with aiofiles.open(cache_file_path, 'wb') as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024):
                    await f.write(chunk)
            logger.info(f"Write Cache file {start_point}-{end_point}: {item_id} has been written, file path: {cache_file_path}")
            
            # 删除写入标签文件并返回成功
            await aiofiles.os.remove(cache_write_tag_path)
            return True

        except Exception as e:
            # 错误处理并删除缓存文件和标签文件
            logger.error(f"Write Cache Error {start_point}-{end_point}: {e}")
            await aiofiles.os.remove(cache_file_path)
            await aiofiles.os.remove(cache_write_tag_path)
            return False

    
def read_cache_file(request_info: RequestInfo) -> AsyncGenerator[bytes, None]:
    """
    读取缓存文件，该函数不是异步的，将直接返回一个异步生成器
    
    :param request_info: 请求信息
    
    :return: function read_file
    """    
    subdirname, dirname = get_hash_subdirectory_from_path(request_info.file_info.path, request_info.item_info.item_type)
    file_dir = os.path.join(cache_path, subdirname, dirname)
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(file_dir):
        if file.startswith('cache_file_') and file.endswith('.tag') is False:
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= request_info.start_byte <= range_end:
                # 调整 end_point 的值
                adjusted_end_point = None if request_info.cache_status == CacheStatus.PARTIAL or request_info.cache_status == CacheStatus.HIT_TAIL else request_info.end_byte - request_info.start_byte
                
                logger.info(f"Read Cache: {os.path.join(file_dir, file)}")

                return read_file(os.path.join(file_dir, file), request_info.start_byte - range_start, adjusted_end_point)
            
    logger.error(f"Read Cache Error: There is no matched cache in the cache directory for this file: {request_info.file_info.path}.")
    return None

def get_cache_status(request_info: RequestInfo) -> bool:
    """
    检查缓存文件是否存在
    
    :param request_info: 请求信息
    """
    subdirname, dirname = get_hash_subdirectory_from_path(request_info.file_info.path, request_info.item_info.item_type)
    cache_dir = os.path.join(cache_path, subdirname, dirname)
    
    if os.path.exists(cache_dir) is False:
        logger.warning(f"Get Cache Error: Cache directory does not exist: {os.path.join(cache_path, subdirname, dirname)}")
        return False
    
    # 检查是否有任何缓存文件正在写入
    for file in os.listdir(cache_dir):
        if file.endswith('.tag'):
            logger.warning(f"Get Cache Error: Cache file is being written: {os.path.join(cache_path, subdirname, dirname, file)}")
            return False
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(cache_dir):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if verify_cache_file(request_info.file_info, (range_start, range_end)):
                if range_start <= request_info.start_byte <= range_end:
                    return True
            else:
                logger.error(f"Get Cache Error: Cache file {file} is invalid, removing..")
                os.remove(os.path.join(cache_dir, file))
                return False
    
    logger.error(f"Get Cache Error: Cache file for range {request_info.start_byte} not found.")
    return False

async def cache_next_episode(request_info: RequestInfo, api_key: str, client: httpx.AsyncClient) -> bool:
    """
    如果是剧集则缓存下一集；如果是电影则跳过
    
    :param request_info: 请求信息
    :param api_key: Emby API Key
    :param client: HTTPX异步客户端
    """
    if request_info.item_info.item_type != 'episode': 
        logger.debug(f"Skip caching next episode for non-episode item: {request_info.item_info.item_id}")
        return False
    
    next_episode_id = request_info.item_info.item_id + 1
    next_item_info = await get_item_info(next_episode_id, api_key, client)
    # 如果找不到下一集，不缓存；非同季度，不缓存
    if next_item_info is not None and next_item_info.season_id == request_info.item_info.season_id:
        next_file_info = await get_file_info(next_item_info.item_id, api_key, media_source_id=None, client=client)
        for file in next_file_info:
            next_request_info = RequestInfo(
                file_info=file,
                item_info=next_item_info,
                host_url=request_info.host_url,
                start_byte=0,
                end_byte=None,
                cache_status=CacheStatus.PARTIAL,
                raw_url_task=asyncio.create_task(get_or_cache_alist_raw_url(file_path=file.path, host_url=request_info.host_url, client=client))

            )
            if get_cache_status(next_request_info):
                logger.debug(f"Skip caching next episode for existing cache: {next_request_info.item_info.item_id}")
                return False
            else:
                await write_cache_file(next_episode_id, next_request_info, client=client)
        return True
    
def verify_cache_file(file_info: FileInfo, cache_file_range: Tuple[int, int]) -> bool:
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