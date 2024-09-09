from datetime import datetime
import hashlib
import os
import re
import requests
from config import *


def get_current_time():
    """获取当前时间，并格式化为包含毫秒的字符串"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

# a wrapper function to get the time of the function
def get_time(func):
    def wrapper(*args, **kwargs):
        import time
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"Function {func.__name__} takes: {end - start} seconds")
        return result
    return wrapper

def get_content_type(container):
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

def get_hash_subdirectory_from_path(file_path):
    """
    计算给定文件路径的MD5哈希，并返回哈希值的前两位作为子目录名称。
    电影：只计算视频文件本身的上层文件夹路径
    电视剧：计算视频文件本身的上两层文件夹路径

    :param file_path: 文件的路径
    :return: 哈希值的前两个字符，作为子目录名称
    """
    match = re.search(r'S\d\dE\d\d', file_path, re.IGNORECASE)
    parts = file_path.split('/')
    # 剧集
    # Example: /mnt/TV/Name/Season 01/Name - S01E01 - Episode Name.mp4 -> Name/Season 01/Name - S01E01 - Episode Name.mp4
    if match:        
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
    """
    if any(file_path.startswith(path) for path in not_redirect_paths):
        print(f"\nFilePath is in notRedirectPaths, return Emby Original Url")
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
            
    if convert_mount_path or convert_special_chars: print(f"\nProcessed FilePath: {file_path}")
    return file_path

def extract_api_key(flask):
    """从请求中提取API密钥"""
    api_key = flask.request.args.get('api_key')
    if not api_key:
        auth_header = flask.request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
    return api_key or emby_key

def get_alist_raw_url(file_path, host_url) -> tuple:
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
        req = requests.post(alist_api_url, json=body, headers=header).json()
    except Exception as e:
        print(e)
        return ('Alist Server Error', 500)
    
    code = req['code']
    
    if code == 200:
        raw_url = req['data']['raw_url']
        # 替换原始URL为反向代理URL
        if alist_download_url_replacement_map:
            for path, url in alist_download_url_replacement_map.items():
                if file_path.startswith(path):
                    if isinstance(url, list):
                        host = re.search(r'(?<=://)[^/]+', host_url).group(0)
                        host_domain = ".".join(host.split('.')[-2:])
                        for u in url:
                            if host_domain in u:
                                url = u
                                break
                        else:
                            # 都不匹配选第一个
                            url = url[0]
                    elif host_url is not None and "{host_url}" in url:
                        url = url.replace("{host_url}/", host_url)
                    
                    if not url.endswith("/"):
                        url = f"{url}/"
                        
                    # 替换原始URL为反向代理URL
                    raw_url = re.sub(r'https?:\/\/[^\/]+\/', url, raw_url)
        
        return (raw_url, 200)
               
    elif code == 403:
        print("403 Forbidden, Please check your Alist Key")
        # return flask.Response(status=403, response="403 Forbidden, Please check your Alist Key")
        return ('403 Forbidden, Please check your Alist Key', 403)
    else:
        print(f"Error: {req['message']}")
        # return flask.Response(status=500, response=req['message'])
        return (req['message'], 500)