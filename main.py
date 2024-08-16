import requests
import flask
import re
from config import *
import os
from datetime import datetime
import concurrent.futures
import hashlib

# 创建全局线程池
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

app = flask.Flask(__name__)

URL_CACHE = {}

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

def get_current_time():
    """获取当前时间，并格式化为包含毫秒的字符串"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

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

    :param file_path: 文件的路径
    :return: 哈希值的前两个字符，作为子目录名称
    """
    hasher = hashlib.md5()
    hasher.update(file_path.encode('utf-8'))
    hash_digest = hasher.hexdigest()
    return hash_digest[:2], hash_digest  # 返回子目录名称和哈希值

# used to get the file info from emby server
def get_file_info(item_id, MediaSourceId, apiKey) -> dict:
    """
    从Emby服务器获取文件信息
    
    :param item_id: Emby Item ID
    :param MediaSourceId: Emby MediaSource ID
    :param apiKey: Emby API Key
    :return: 包含文件信息的字典
    """
    data = {}
    url = f"{emby_server}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={MediaSourceId}&api_key={apiKey}"
    print("\n" + url)
    req = requests.get(url).json()
    for i in req['MediaSources']:
        # print(i)
        if i['Id'] == MediaSourceId:
            data['Status'] = "Success"
            data['Path'] = i['Path']
            # data['DirectStreamUrl'] = i['DirectStreamUrl']
            data['Protocol'] = i['Protocol']
            data['Bitrate'] = i['Bitrate'] 
            data['Size'] = i['Size']
            data['Container'] = i['Container']
            return data
    
    data['Status'] = "Error"
    data['Message'] = "Can't match MediaSourceId"
    return data

def transform_file_path(file_path, mount_path_prefix=mount_path_prefix):
    """
    转换 rclone 文件路径，以匹配Alist的路径格式
    
    :param file_path: 文件路径
    :param mount_path_prefix: 挂载路径前缀
    :return: 转换后的文件路径
    """
    if convert_mount_path:
        if mount_path_prefix.endswith("/"):
            mount_path_prefix = mount_path_prefix.rstrip("/")
        if file_path.startswith(mount_path_prefix):
            file_path = file_path[len(mount_path_prefix):]
        else:
            print(f"Error: mount_path_prefix: {mount_path_prefix} is not in filePath: {file_path}\nPlease check your mount_path_prefix configuration in main.py")
            
    if convert_special_chars:
        for char in special_chars_list:
            if char in file_path:
                file_path = file_path.replace(char, '‛'+char)
            
    if convert_mount_path or convert_special_chars: print(f"\nProcessed FilePath: {file_path}")
    return file_path

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

def read_file(file_path, startPoint=0, endPoint=None, chunk_size=1024*1024):
    """
    读取文件的指定范围，并返回生成器
    
    :param filePath: 文件路径
    :param startPoint: 文件读取起始点
    :param endPoint: 文件读取结束点，None 表示文件末尾
    :param chunk_size: 每次读取的字节数，默认为 1MB
    :return: 生成器，每次返回 chunk_size 大小的数据
    """
    try:
        with open(file_path, 'rb') as f:
            f.seek(startPoint)
            data = f.read(chunk_size)
            if endPoint is not None:
                while data and f.tell() <= endPoint:
                        if f.tell() > endPoint:
                            yield data[:endPoint - f.tell() + len(data)]
                        else:
                            yield data
                        data = f.read(chunk_size)
            else:
                while data:
                    yield data
                    data = f.read(chunk_size)
    except (FileNotFoundError, PermissionError) as e:
        print(f"Error opening file: {e}")


def write_cache_file(item_id, path, req_header, size=52428800, start_point=0, file_size=None) -> bool:
    """
    写入缓存文件
    
    :param item_id: Emby Item ID
    :param path: 文件路径
    :param req_header: 请求头
    :param size: 缓存文件大小，默认为 50MB
    :param start_point: 缓存文件的起始点
    :param file_size: 文件大小
    :return: 缓存是否成功
    """
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    # 如果filesize 不为 None，endPoint 为文件末尾
    if start_point <= size:
        start_point = 0
        end_point = size - 1
    elif file_size is not None:
        end_point = file_size - 1
    else:
        print(f"{get_current_time()}-Cache Error {start_point}-{end_point}, File Size is None")
        return
    
    # 获取Alist Raw Url
    raw_url, code = get_alist_raw_url(path)
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
                    print(f"{get_current_time()}-Cache Error: Cache file for range {start_point} may is still writing.")
                    return False
                print(f"{get_current_time()}-WARNING: Existing Cache Range within new range. Deleting old cache.")
                os.remove(os.path.join(cache_path, subdirname, dirname, file))
    
    # 创建一个空文件 防止后续被重复缓存
    with open(cache_file_path, 'w') as f:
        pass
    
    req_header = dict(req_header) # Copy the headers
    req_header['Host'] = raw_url.split('/')[2]
      
    # Modify the range to startPoint-first50M
    req_header['Range'] = f"bytes={start_point}-{end_point}"

    # 如果请求失败，删除空缓存文件
    try:
        resp = requests.get(raw_url, headers=req_header, stream=True)
    except Exception as e:
        print(f"{get_current_time()}-Cache Error {start_point}-{end_point}: {e}")
        os.remove(cache_file_path)
        return False
    
    if resp.status_code == 206: 
        # print(f"Start to write cache file: {item_id}")
        with open (cache_file_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024):
                f.write(chunk)
                
        print(f"{get_current_time()}-Cache file {start_point}-{end_point}: {item_id} has been written, file path: {cache_file_path}")
        return True
    else:
        print(f"{get_current_time()}-Cache Error {start_point}-{end_point}: Upstream return code: {resp.status_code}")
        os.remove(cache_file_path)
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
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= start_point <= range_end:
                if end_point is not None and end_point > range_end:
                    return read_file(os.path.join(cache_path, subdirname, dirname, file), start_point-range_start, None)
                elif end_point is not None and end_point <= range_end:
                    return read_file(os.path.join(cache_path, subdirname, dirname, file), start_point-range_start, end_point-start_point)
                else:
                    return read_file(os.path.join(cache_path, subdirname, dirname, file), start_point-range_start, end_point)
    print(f"{get_current_time()}-Cache Error: There is no cache file in the cache directory: {path}.")
    return False

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
        print(f"{get_current_time()}-Cache Error: Cache directory does not exist: {os.path.join(cache_path, subdirname, dirname)}")
        return False
    
    # 查找与 startPoint 匹配的缓存文件，endPoint 为文件名的一部分
    for file in os.listdir(os.path.join(cache_path, subdirname, dirname)):
        if file.startswith('cache_file_'):
            range_start, range_end = map(int, file.split('_')[2:4])
            if range_start <= start_point <= range_end:
                return True
    
    print(f"{get_current_time()}-Cache Error: Cache file for range {start_point} not found.")
    return False

def extract_api_key():
    """从请求中提取API密钥"""
    api_key = flask.request.args.get('api_key')
    if not api_key:
        auth_header = flask.request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                api_key = match_token.group(1)
    return api_key or emby_key

def get_alist_raw_url(file_path) -> tuple:
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
                    protocol, rest = raw_url.split("://", 1)
                    domain, path = rest.split("/", 1)
                    if not url.endswith("/"):
                        raw_url = f"{url}/{path}"
                    else:
                        raw_url = f"{url}{path}"
        
        return (raw_url, 200)
               
    elif code == 403:
        print("403 Forbidden, Please check your Alist Key")
        # return flask.Response(status=403, response="403 Forbidden, Please check your Alist Key")
        return ('403 Forbidden, Please check your Alist Key', 403)
    else:
        print(f"Error: {req['message']}")
        # return flask.Response(status=500, response=req['message'])
        return (req['message'], 500)

# return Alist Raw Url or Emby Original Url
@get_time
def redirect_to_alist_raw_url(file_path) -> flask.Response:
    """获取视频直链地址"""
    
    if file_path in URL_CACHE.keys():
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[file_path]['time'] < 300:
            print("\nAlist Raw URL Cache exists and is valid (less than 5 minutes)")
            print("Redirected Url: " + URL_CACHE[file_path]['url'])
            return flask.redirect(URL_CACHE[file_path]['url'], code=302)
        else:
            print("\nAlist Raw URL Cache is expired, re-fetching...")
            del URL_CACHE[file_path]
    
    raw_url, code = get_alist_raw_url(file_path)
    
    if code == 200:
        URL_CACHE[file_path] = {
            'url': raw_url,
            'time': datetime.now().timestamp()
        }
        print("Redirected Url: " + raw_url)
        return flask.redirect(raw_url, code=302)
    else:
        print(f"Error: {raw_url}")
        return flask.Response(status=code, response=raw_url)
    

# for infuse
@app.route('/Videos/<item_id>/<filename>', methods=['GET'])
# for emby
@app.route('/videos/<item_id>/<filename>', methods=['GET'])
@app.route('/emby/videos/<item_id>/<filename>', methods=['GET'])
def redirect(item_id, filename):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx
    
    api_key = extract_api_key()
    file_info = get_file_info(item_id, flask.request.args.get('MediaSourceId'), api_key)
    
    if file_info['Status'] == "Error":
        print(file_info['Message'])
        return flask.Response(status=500, response=file_info['Message'])
    
    
    print(f"\n{get_current_time()} - Requested Item ID: {item_id}")
    print("MediaFile Mount Path: " + file_info['Path'])
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info['Path']):
        redirected_url = f"{emby_public_URL}/preventRedirect{flask.request.full_path}"
        print("Redirected Url: " + redirected_url)
        return flask.redirect(redirected_url, code=302)
    
    alist_path = transform_file_path(file_info['Path'])
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enable_cache:
        return redirect_to_alist_raw_url(alist_path)

    range_header = flask.request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        print("\nWarning: Range header is not correctly formatted.")
        print(flask.request.headers)
        return redirect_to_alist_raw_url(alist_path)
    
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    # start_byte = int(bytes_range.split('-')[0])
    if bytes_range.endswith('-'):
        start_byte = int(bytes_range[:-1])
        end_byte = None
    else:
        start_byte, end_byte = map(int, bytes_range.split('-'))
    
    # 获取缓存15秒的文件大小， 并取整
    cacheFileSize = int(file_info.get('Bitrate', 52428800) / 8 * 15)
    
    if start_byte < cacheFileSize:
        
        # 判断客户端是否在黑名单中
        if any(user_agent.lower() in flask.request.headers.get('User-Agent', '').lower() for user_agent in cache_client_blacklist):
                print("Cache is disabled for this client")
                return redirect_to_alist_raw_url(alist_path)
        

        # 响应头中的end byte
        resp_end_byte = cacheFileSize - 1
        resp_file_size = resp_end_byte - start_byte + 1

        
        getCacheStatus_exists = get_cache_status(item_id, alist_path, start_byte)
        if getCacheStatus_exists:
            
            resp_headers = {
                'Content-Type': get_content_type(file_info['Container']),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}",
                'Content-Length': f'{resp_file_size}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            print(range_header)
            return flask.Response(read_cache_file(item_id, alist_path, start_byte, cacheFileSize), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(write_cache_file, item_id, alist_path, flask.request.headers, cacheFileSize, start_byte)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return redirect_to_alist_raw_url(alist_path)
     
    # 当请求文件末尾章节信息时
    elif file_info['Size'] - start_byte < 2 * 1024 * 1024:
        if get_cache_status(item_id, path=alist_path, start_point=start_byte):
            if end_byte is None:
                resp_end_byte = file_info['Size'] - 1
                resp_file_size = file_info['Size'] - start_byte
            else:
                resp_end_byte = end_byte
                resp_file_size = end_byte - start_byte + 1

            resp_headers = {
                'Content-Type': get_content_type(file_info['Container']),
                'Accept-Ranges': 'bytes',
                'Content-Range': f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}",
                'Content-Length': f'{resp_file_size}',
                'Cache-Control': 'private, no-transform, no-cache',
                'X-EmbyToAList-Cache': 'Hit',
            }
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            print(range_header)
            return flask.Response(read_cache_file(item_id=item_id, path=alist_path, start_point=start_byte, end_point=end_byte), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(write_cache_file, item_id, alist_path, flask.request.headers, 0, start_byte, file_info['Size'])
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return redirect_to_alist_raw_url(alist_path)
    else:
        print(range_header)
        return redirect_to_alist_raw_url(alist_path)


if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')