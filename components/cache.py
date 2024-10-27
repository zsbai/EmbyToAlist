import asyncio
import os
from weakref import WeakValueDictionary

import aiofiles
import aiofiles.os
import httpx

from components.utils import *
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
    auto_delete=False
    ) -> AsyncGenerator[bytes, None]:
    """
    读取文件的指定范围，并返回异步生成器。
   
    :param file_path: 缓存文件路径
    :param start_point: 文件读取起始点
    :param end_point: 文件读取结束点，None 表示文件末尾
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
        print(f"File not found: {file_path}")
    except PermissionError:
        print(f"Permission denied: {file_path}")
    except IOError as e:
        print(f"IO error occurred while reading file: {e}")
    except Exception as e:
        print(f"Unexpected error occurred while reading file: {e}")
    finally:
        if auto_delete:
            await aiofiles.os.remove(file_path)
            print(f"Cache File Auto Deleted: {file_path}")


async def write_cache_file(item_id, path, req_header=None, cache_size=52428800, start_point=0, file_size=None, host_url=None, client: httpx.AsyncClient=None) -> bool:
    """
    写入缓存文件，end point通过cache_size计算得出
    
    :param item_id: Emby Item ID
    :param path: 文件路径, 用于获取Alist Raw Url
    :param req_header: 请求头，用于请求Alist Raw Url
    :param cache_size: 缓存文件大小，默认为 50MB
    :param start_point: 缓存文件的起始点
    :param file_size: 文件大小
    :param host_url: 请求中请求头的host
    ::param client: HTTPX异步客户端
    
    :return: 缓存是否成功
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    # 计算缓存文件的结束点
    # 如果filesize 不为 None，endPoint 为文件末尾（缓存尾部元数据）
    if start_point <= cache_size:
        start_point = 0
        end_point = cache_size - 1
    elif file_size is not None:
        end_point = file_size - 1
    else:
        print(f"{get_current_time()}-Cache Error {start_point}, File Size is None")
        return
    
    # 获取Alist Raw Url
    code, raw_url = await get_alist_raw_url(path, host_url, client)
    if code != 200:
        print(f"{get_current_time()}-Cache Error {start_point}-{end_point}, Alist Return: code: {code} and url: {raw_url}")
        return False
    
    # 根据起始点和缓存大小确定缓存文件路径
    cache_file_name = f'cache_file_{start_point}_{end_point}'
    cache_file_path = os.path.join(cache_path, subdirname, dirname, cache_file_name)
    print(f"\n {get_current_time()} - Start to cache file {start_point}-{end_point}: {item_id}, file path: {cache_file_path}")
    
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
                    print(f"{get_current_time()}-WARNING: Cache Range Already Exists. Abort.")
                    await aiofiles.os.remove(cache_write_tag_path)
                    return False
                elif start_point <= file_range_start and end_point >= file_range_end:
                    print(f"{get_current_time()}-WARNING: Existing Cache Range within new range. Deleting old cache.")
                    await aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
        
        # 请求Alist Raw Url，好像请求头没太所谓
        if req_header is None:
            req_header = {}
        else:
            req_header = dict(req_header) # Copy the headers
            
        req_header['host'] = raw_url.split('/')[2]
        # Modify the range to startPoint-first50M
        req_header['range'] = f"bytes={start_point}-{end_point}"

        # 如果请求失败，删除空缓存文件
        try:
            resp = await client.get(raw_url, headers=req_header)
        except Exception as e:
            print(f"{get_current_time()}-Write Cache Error {start_point}-{end_point}: {e}")
            await aiofiles.os.remove(cache_file_path)
            await aiofiles.os.remove(cache_write_tag_path)
            return False
        
        if resp.status_code == 206: 
            try:
                # print(f"Start to write cache file: {item_id}")
                async with aiofiles.open(cache_file_path, 'wb') as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024):
                        await f.write(chunk)
                        
                print(f"{get_current_time()}-Write Cache file {start_point}-{end_point}: {item_id} has been written, file path: {cache_file_path}")
                
                await aiofiles.os.remove(cache_write_tag_path)
                return True
            except Exception as e:
                print(f"{get_current_time()}-Write Cache Error {start_point}-{end_point}: {e}")
                await aiofiles.os.remove(cache_file_path)
                await aiofiles.os.remove(cache_write_tag_path)
                return False
        else:
            print(f"{get_current_time()}-Write Cache Error {start_point}-{end_point}: Upstream return code: {resp.status_code}")
            await aiofiles.os.remove(cache_file_path)
            await aiofiles.os.remove(cache_write_tag_path)
            return False
    
def read_cache_file(item_id, path, start_point=0, end_point=None):
    """
    读取缓存文件
    
    :param item_id: Emby Item ID
    :param path: 文件路径
    :param start_point: 缓存文件的起始点
    :param end_point: 缓存文件的结束点
    
    :return: 缓存文件的内容
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    file_dir = os.path.join(cache_path, subdirname, dirname)
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(file_dir):
            
        if file.startswith('cache_file_') and file.endswith('.tag') is False:
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= start_point <= range_end:
                # 调整 end_point 的值
                adjusted_end_point = None if end_point is None or end_point > range_end else end_point - start_point
                
                print(f"{get_current_time()}-Read Cache: {os.path.join(file_dir, file)}")

                return read_file(os.path.join(file_dir, file), start_point-range_start, adjusted_end_point)
            
    print(f"{get_current_time()}-Read Cache Error: There is no cache file in the cache directory: {path}.")
    return None

def get_cache_status(item_id, path, start_point=0) -> bool:
    """
    检查缓存文件是否存在
    
    :param item_id: Emby Item ID
    :param path: 文件路径
    :param start_point: 缓存文件的起始点
    :return: 缓存文件是否存在
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    cache_dir = os.path.join(cache_path, subdirname, dirname)
    
    if os.path.exists(cache_dir) is False:
        print(f"{get_current_time()}-Get Cache Error: Cache directory does not exist: {os.path.join(cache_path, subdirname, dirname)}")
        return False
    
    # 检查是否有任何缓存文件正在写入
    for file in os.listdir(cache_dir):
        if file.endswith('.tag'):
            print(f"{get_current_time()}-Get Cache Error: Cache file is being written: {os.path.join(cache_path, subdirname, dirname, file)}")
            return False
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(cache_dir):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= start_point <= range_end:
                return True
    
    print(f"{get_current_time()}-Get Cache Error: Cache file for range {start_point} not found.")
    return False

async def delete_cache_file(item_id, path, start_point=0):
    """
    删除range包含start_point的缓存文件
    """
    if not get_cache_status(item_id, path, start_point):
        print(f"{get_current_time()}-Delete Cache Error: Cache file for range {start_point} not found.")
        return False
    
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= start_point <= range_end:
                await aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
                print(f"{get_current_time()}-Delete Cache file {range_start}-{range_end}: {item_id}")
                return True
        
    print(f"{get_current_time()}-Delete Cache Error: Cache file for range {start_point} not found.")
    return False