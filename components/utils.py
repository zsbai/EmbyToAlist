import hashlib
import os
import re
import urllib.parse

import fastapi
import httpx
from uvicorn.server import logger

from config import *
from components.models import *
from typing import AsyncGenerator, Tuple

# a wrapper function to get the time of the function
def get_time(func):
    def wrapper(*args, **kwargs):
        import time
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger.info(f"Function {func.__name__} takes: {end - start} seconds")
        return result
    return wrapper

def get_content_type(container) -> str:
    """文件格式对应的Content-Type映射"""
    content_types = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska',
        'ts': 'video/mp2t',
    }

    # 返回对应的Content-Type，如果未找到，返回一个默认值
    return content_types.get(container.lower(), 'application/octet-stream')

def get_hash_subdirectory_from_path(file_path, media_type) -> Tuple[str, str]:
    """
    计算给定文件路径的MD5哈希，并返回哈希值的前两位作为子目录名称。
    电影：只计算视频文件本身的上层文件夹路径
    电视剧：计算视频文件本身的上两层文件夹路径

    :param file_path: 文件的路径
    :param media_type: 媒体类型，电影或剧集
    
    :return: 哈希值的前两个字符，作为子目录名称
    """
    parts = file_path.split('/')
    # 剧集
    # Example: /mnt/TV/Name/Season 01/Name - S01E01 - Episode Name.mp4 -> Name/Season 01/Name - S01E01 - Episode Name.mp4
    if media_type != 'movie':        
        file_path: str = os.path.join("series", os.path.join(parts[-3], parts[-2], parts[-1]))
    # 电影
    # Example: /mnt/Movies/Name (Year)/Name (Year).mp4 -> Name (Year)/Name (Year).mp4
    else:
        file_path: str = os.path.join("movie", os.path.join(parts[-2], parts[-1]))
        
    hasher = hashlib.md5()
    hasher.update(file_path.encode('utf-8'))
    hash_digest = hasher.hexdigest()
    return hash_digest[:2], hash_digest  # 返回子目录名称和哈希值

# True means return Alist Raw Url, False means return Emby Original Url
def should_redirect_to_alist(file_path: str) -> bool:
    """
    检查文件路径是否在不需要重定向的路径中
    
    :param file_path: 系统文件路径，非alist路径
    """
    if any(file_path.startswith(path) for path in not_redirect_paths):
        logger.debug(f"File Path is in notRedirectPaths, return Emby Original Url")
        return False
    else:
        return True

def transform_file_path(file_path, mount_path_prefix_remove=mount_path_prefix_remove, mount_path_prefix_add=mount_path_prefix_add) -> str:
    """
    转换 rclone 文件路径，以匹配Alist的路径格式
    rclone挂载路径->Alist路径
    
    :param file_path: 文件路径
    :param mount_path_prefix_remove: 需要移除挂载路径前缀
    :param mount_path_prefix_add: 需要添加挂载路径前缀
    :return: 转换后的文件路径
    """
    try:
        mount_path_prefix_remove = mount_path_prefix_remove.removesuffix("/")
        mount_path_prefix_add = mount_path_prefix_add.removesuffix("/")

        if file_path.startswith(mount_path_prefix_remove):
            file_path = file_path[len(mount_path_prefix_remove):]

        if mount_path_prefix_add:
            file_path = mount_path_prefix_add + file_path
    except Exception as e:
        print(f"Error: convert_mount_path failed, {e}")
            
    if convert_special_chars:
        for char in special_chars_list:
            if char in file_path:
                file_path = file_path.replace(char, '‛'+char)
            
    if convert_mount_path or convert_special_chars: logger.debug(f"Processed File Path: {file_path}")
    return file_path

def extract_api_key(request: fastapi.Request):
    """从请求中提取API密钥"""
    api_key = request.query_params.get('api_key') or request.query_params.get('X-Emby-Token')
    if not api_key:
        auth_header = request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
    return api_key or emby_key

async def get_alist_raw_url(file_path, host_url, client: httpx.AsyncClient) -> str:
    """根据文件路径获取Alist Raw Url"""
    
    alist_api_url = f"{alist_server}/api/fs/get"

    body = {
        "path": file_path,
        "password": ""
    }
    header = {
        "Authorization": alist_key,
        "Content-Type": "application/json;charset=UTF-8"
    }
    
    try:
        req = await client.post(alist_api_url, json=body, headers=header)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Error: get_alist_raw_url failed, {e}")
        return ('Alist Server Error', 500)
    
    code = req['code']
    
    if code == 200:
        raw_url = req['data']['raw_url']
        # 替换原始URL为反向代理URL
        if alist_download_url_replacement_map:
            for path, url in alist_download_url_replacement_map.items():
                if file_path.startswith(path):
                    if isinstance(url, list):
                        hostname = urllib.parse.urlparse(host_url).hostname

                        for u in url:
                            if hostname in u:
                                url = u
                                break
                        else:
                            # 都不匹配选第一个
                            url = url[0]
                    # 如果URL中包含{host_url}，则替换为host_url
                    elif host_url is not None and "{host_url}" in url:
                        url = url.replace("{host_url}/", host_url)
                    
                    if not url.endswith("/"):
                        url = f"{url}/"
                        
                    # 替换原始URL为反向代理URL
                    raw_url = re.sub(r'https?:\/\/[^\/]+\/', url, raw_url)
        
        return raw_url
               
    elif code == 403:
        logger.error("Alist server response 403 Forbidden, Please check your Alist Key")
        # return 403, '403 Forbidden, Please check your Alist Key'
        raise fastapi.HTTPException(status_code=500, detail="Alist return 403 Forbidden, Please check your Alist Key")
    else:
        logger.error(f"Error: {req['message']}")
        # return 500, req['message']        
        raise fastapi.HTTPException(status_code=500, detail="Alist Server Error")
    
# used to get the file info from emby server
async def get_file_info(item_id, api_key, media_source_id, client: httpx.AsyncClient) -> FileInfo:
    """
    从Emby服务器获取文件信息
    
    :param item_id: Emby Item ID
    :param MediaSourceId: Emby MediaSource ID
    :param apiKey: Emby API Key
    :return: 包含文件信息的字典
    """
    media_info_api = f"{emby_server}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={media_source_id}&api_key={api_key}"
    logger.info(f"Requested Info URL: {media_info_api}")
    try:
        media_info = await client.get(media_info_api)
        media_info.raise_for_status()
        media_info = media_info.json()
    except Exception as e:
        logger.error(f"Error: failed to request Emby server, {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to request Emby server, {e}")

    if media_source_id is None:
        all_source = []
        for i in media_info['MediaSources']:
            all_source.append(FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            ))
        return all_source

    for i in media_info['MediaSources']:
        if i['Id'] == media_source_id:
            return FileInfo(
                path=transform_file_path(i.get('Path')),
                bitrate=i.get('Bitrate', 27962026),
                size=i.get('Size', 0),
                container=i.get('Container', None),
                # 获取15秒的缓存文件大小， 并取整
                cache_file_size=int(i.get('Bitrate', 27962026) / 8 * 15)
            )
    # can't find the matched MediaSourceId in MediaSources
    raise fastapi.HTTPException(status_code=500, detail="Can't match MediaSourceId")
    

async def get_item_info(item_id, api_key, client) -> ItemInfo:
    item_info_api = f"{emby_server}/emby/Items?api_key={api_key}&Ids={item_id}"
    logger.debug(f"Requesting Item Info: {item_info_api}")
    try:
        req = await client.get(item_info_api)
        req.raise_for_status()
        req = req.json()
    except Exception as e:
        logger.error(f"Error: get_item_info failed, {e}")
        raise fastapi.HTTPException(status_code=500, detail="Failed to request Emby server, {e}")
    
    if not req['Items']: 
        logger.debug(f"Item not found: {item_id};")
        return None
    
    item_type = req['Items'][0]['Type'].lower()
    if item_type != 'movie': item_type = 'episode'
    season_id = int(req['Items'][0]['SeasonId']) if item_type == 'episode' else None

    return ItemInfo(
        item_id=int(item_id),
        item_type=item_type,
        season_id=season_id
    )

async def reverse_proxy(cache: AsyncGenerator[bytes, None],
                        url_task: str,
                        request_header: dict,
                        response_headers: dict,
                        client: httpx.AsyncClient,
                        status_code: int = 206
                        ):
    """
    读取缓存数据和URL，返回合并后的流

    :param cache: 缓存数据
    :param url_task: 源文件的URL的异步任务
    :param request_header: 请求头，用于请求直链，包含host和range
    :param response_headers: 返回的响应头，包含调整过的range以及content-type
    :param client: HTTPX异步客户端
    :param status_code: HTTP响应状态码，默认为206
    
    :return: fastapi.responses.StreamingResponse
    """
    try:
        async def merged_stream():
            if cache is not None:
                async for chunk in cache:
                    yield chunk
                logger.info("Cache exhausted, streaming from source")
            raw_url = await url_task
            
            request_header['host'] = raw_url.split('/')[2]
            async with client.stream("GET", raw_url, headers=request_header) as response:
                response.raise_for_status()
                if status_code == 206 and response.status_code != 206:
                    raise ValueError(f"Expected 206 response, got {response.status_code}")
                async for chunk in response.aiter_bytes():
                    yield chunk

        return fastapi.responses.StreamingResponse(
            merged_stream(), 
            headers=response_headers, 
            status_code=status_code
            )
        
    except Exception as e:
        logger.error(f"Reverse_proxy failed, {e}")
        raise fastapi.HTTPException(status_code=500, detail="Reverse Proxy Failed")