from datetime import datetime
import os
from components.utils import *
import httpx
import aiofiles
from typing import AsyncGenerator, Optional


async def read_file(
    file_path: str, 
    start_point: int = 0, 
    end_point: Optional[int] = None, 
    chunk_size: int = 1024*1024, 
    auto_delete=False
    ) -> AsyncGenerator[bytes, None]:
    """
    读取文件的指定范围，并返回异步生成器
   
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
                    remaining = end_point - f.tell()
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
    :param size: 缓存文件大小，默认为 50MB
    :param start_point: 缓存文件的起始点
    :param file_size: 文件大小
    :return: 缓存是否成功
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    # 如果filesize 不为 None，endPoint 为文件末尾
    if start_point <= cache_size:
        start_point = 0
        end_point = cache_size - 1
    elif file_size is not None:
        end_point = file_size - 1
    else:
        print(f"{get_current_time()}-Cache Error {start_point}-{end_point}, File Size is None")
        return
    
    # 获取Alist Raw Url
    raw_url, code = await get_alist_raw_url(path, host_url, client)
    if code != 200:
        print(f"{get_current_time()}-Cache Error {start_point}-{end_point}, Alist Return: code: {code} and url: {raw_url}")
        return False
    
    # 根据起始点和缓存大小确定缓存文件路径
    cache_file_path = os.path.join(cache_path, subdirname, dirname, f'cache_file_{start_point}_{end_point}')
    print(f"\n {get_current_time()} - Start to cache file {start_point}-{end_point}: {item_id}, file path: {cache_file_path}")
    
    os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)
    
    # 检查是否已有包含当前范围的缓存文件
    for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            
            if start_point >= range_start and end_point <= range_end:
                print(f"{get_current_time()}-WARNING: Cache Range Already Exists. Abort.")
                return False
            elif start_point <= range_start and end_point >= range_end:
                full_path = os.path.join(cache_path, subdirname, dirname, file)
                mod_time = os.path.getmtime(full_path)
                now_time = datetime.now().timestamp()
                # 如果文件在过去15秒内被修改过，可能仍在缓存过程中
                # 防止重复缓存由write_cache_file负责
                if now_time - mod_time < 15:
                    print(f"{get_current_time()}-Write Cache Error: Cache file for range {start_point} may is still writing.")
                    return False
                print(f"{get_current_time()}-WARNING: Existing Cache Range within new range. Deleting old cache.")
                aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
    
    # 创建一个空文件 防止后续被重复缓存
    with open(cache_file_path, 'w') as f:
        pass
    
    # 请求Alist Raw Url，好像请求头没太所谓
    if req_header is None:
        req_header = {}
    else:
        req_header = dict(req_header) # Copy the headers
        
    req_header['Host'] = raw_url.split('/')[2]
      
    # Modify the range to startPoint-first50M
    req_header['Range'] = f"bytes={start_point}-{end_point}"

    # 如果请求失败，删除空缓存文件
    try:
        resp = client.get(raw_url, headers=req_header)
    except Exception as e:
        print(f"{get_current_time()}-Write Cache Error {start_point}-{end_point}: {e}")
        aiofiles.os.remove(cache_file_path)
        return False
    
    if resp.status_code == 206: 
        # print(f"Start to write cache file: {item_id}")
        async with aiofiles.open(cache_file_path, 'wb') as f:
            async for chunk in resp.aiter_content(chunk_size=1024):
                await f.write(chunk)
                
        print(f"{get_current_time()}-Write Cache file {start_point}-{end_point}: {item_id} has been written, file path: {cache_file_path}")
        return True
    else:
        print(f"{get_current_time()}-Write Cache Error {start_point}-{end_point}: Upstream return code: {resp.status_code}")
        aiofiles.os.remove(cache_file_path)
        return False
    
def read_cache_file(item_id, path, start_point=0, end_point=None):
    """
    读取缓存文件
    
    :param item_id: Emby Item ID
    :param path: 文件路径
    :param start_point: 缓存文件的起始点
    :param end_point: 缓存文件的结束点
    :param auto_delete: 是否在读取完后删除文件
    :return: 缓存文件的内容
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    # 应该为start point标记，如果不为None，则读取完后删除文件
    # 需要删除的缓存文件的起始点，如果包含则删除
    cache_delete_start_point_tag = None
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
        if file.startswith('cache_delete_tag_'):
            cache_delete_start_point_tag = int(file.split('_')[-1])
            # 删除标记文件
            aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
            
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= start_point <= range_end:
                # 调整 end_point 的值
                adjusted_end_point = None if end_point is None or end_point > range_end else end_point - start_point
                
                # 检查是否需要删除
                auto_delete = cache_delete_start_point_tag is not None and range_start <= cache_delete_start_point_tag <= range_end
                return read_file(os.path.join(cache_path, subdirname, dirname, file), start_point-range_start, adjusted_end_point, auto_delete=auto_delete)
            
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
    
    if os.path.exists(os.path.join(cache_path, subdirname, dirname)) is False:
        print(f"{get_current_time()}-Get Cache Error: Cache directory does not exist: {os.path.join(cache_path, subdirname, dirname)}")
        return False
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
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
                aiofiles.os.remove(os.path.join(cache_path, subdirname, dirname, file))
                print(f"{get_current_time()}-Delete Cache file {range_start}-{range_end}: {item_id}")
                return True
        
    print(f"{get_current_time()}-Delete Cache Error: Cache file for range {start_point} not found.")
    return False

def create_cache_delete_tag(item_id, path, start_point=0):
    """
    创建一个空文件，用于标记缓存文件应该在使用完后删除
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    if not os.path.exists(os.path.join(cache_path, subdirname, dirname)):
        print(f"{get_current_time()}-Create Cache Delete Tag Error: Cache directory does not exist: {os.path.join(cache_path, subdirname, dirname)}")
        return False
    
    with open(os.path.join(cache_path, subdirname, dirname, f'cache_delete_tag_{start_point}'), 'w') as f:
        pass