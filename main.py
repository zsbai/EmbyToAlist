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

# 获取当前时间，并格式化为包含毫秒的字符串
def getCurrentTime():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

def get_content_type(container):
    # 容器映射到Content-Type
    content_types = {
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'ogg': 'video/ogg',
        'avi': 'video/x-msvideo',
        'mpeg': 'video/mpeg',
        'mov': 'video/quicktime',
        'mkv': 'video/x-matroska'
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
def GetFileInfo(item_id, MediaSourceId, apiKey) -> dict:
    data = {}
    url = f"{embyServer}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={MediaSourceId}&api_key={apiKey}"
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

def optimizeFilePath(filePath, mountPathPrefix=mountPathPrefix):
    if convertMountPath:
        if mountPathPrefix.endswith("/"):
            mountPathPrefix = mountPathPrefix.rstrip("/")
        if filePath.startswith(mountPathPrefix):
            filePath = filePath[len(mountPathPrefix):]
        else:
            print(f"Error: mountPathPrefix: {mountPathPrefix} is not in filePath: {filePath}\nPlease check your mountPathPrefix configuration in main.py")
            
    if convertSpecialChars:
        for char in specialCharsList:
            if char in filePath:
                filePath = filePath.replace(char, '‛'+char)
            
    if convertMountPath or convertSpecialChars: print(f"\nProcessed FilePath: {filePath}")
    return filePath

# True means return Alist Raw Url, False means return Emby Original Url
def checkFilePath(filePath: str) -> bool:
    """检查文件路径是否在notRedirectPaths中"""
    for path in notRedirectPaths:
        if filePath.startswith(path):
            print(f"\nFilePath is in notRedirectPaths, return Emby Original Url")
            return False
    return True


def putCacheFile(item_id, path, headers, size=52428800, startPoint=0) -> bool:
    """缓存文件"""
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    
    if startPoint == 0:
        cache_file_path = os.path.join(cachePath, subdirname, dirname, 'cache_file')
        size = size - 1
        name = ', Cache file size is ' + size
    else:
        cache_file_path = os.path.join(cachePath, subdirname, dirname, f'cache_file_{startPoint}')
        size = ''
        name = startPoint
    print(f"\n {getCurrentTime()} - Start to cache file {name}: {item_id}")
    if os.path.exists(cache_file_path):
        print(f"{getCurrentTime()}-WARNING: Cache File Already Exists or Cache File is being written. Abort.")
        return False
    else:
        os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)
    
    # 获取Alist Raw Url
    raw_url, code = getAlistURL(path)
    if code != 200:
        print(f"{getCurrentTime()}-Cache Error {name}, Alist Return: {raw_url}")
        return False
    
    headers = dict(headers) # Copy the headers
    headers['Host'] = raw_url.split('/')[2]
      
    # Modify the range to startPoint-first50M
    headers['Range'] = f"bytes={startPoint}-{size}"

    resp = requests.get(raw_url, headers=headers, stream=True)
    if resp.status_code == 206: 
        # print(f"Start to write cache file: {item_id}")
        with open (cache_file_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024):
                f.write(chunk)
                
        print(f"{getCurrentTime()}-Cache file {name}: {item_id} has been written")
        return True
    else:
        print(f"{getCurrentTime()}-Cache Error {name}: Upstream return code: {resp.status_code}")
        return False
    
def getCacheFile(item_id, path, name=''):
    """读取缓存文件"""
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    if name:
        name = f'cache_file_{name}'
    else:
        name = 'cache_file'
        
    path = os.path.join(cachePath, subdirname, dirname, name)
    with open(f'{path}', 'rb') as f:
        data = f.read(1024 * 1024)
        while data:
            yield data
            data = f.read(1024 * 1024)

def getCacheStatus(item_id, path, name='') -> tuple:
    """检查缓存文件是否存在，并检查其最后修改时间"""
    subdirname, dirname = get_hash_subdirectory_from_path(path)
    if name:
        name = f'cache_file_{name}'
    else:
        name = 'cache_file'

    cache_file_path = os.path.join(cachePath, subdirname, dirname, name)
    
    if os.path.exists(cache_file_path):
        # 获取文件最后修改时间
        mod_time = os.path.getmtime(cache_file_path)
        now_time = datetime.now().timestamp()
        
        # 如果文件在过去15秒内被修改过，可能仍在缓存过程中
        # 防止重复缓存由putCacheFile负责
        if now_time - mod_time < 15:
            return False
        else:
            return True
    else:
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

def getAlistURL(filePath) -> tuple:
    """获取Alist Raw Url"""
    
    alistApiUrl = f"{alistServer}/api/fs/get"
    body = {
        "path": filePath,
        "password": ""
    }
    header = {
        "Authorization": alist_key,
        "Content-Type": "application/json;charset=UTF-8"
    }
    
    try:
        req = requests.post(alistApiUrl, json=body, headers=header).json()
    except Exception as e:
        print(e)
        return ('Alist Server Error', 500)
    
    code = req['code']
    
    if code == 200:
        raw_url = req['data']['raw_url']
        # 替换原始URL为反向代理URL
        if AlistPublicStorageURL:
            protocol, rest = raw_url.split("://", 1)
            domain, path = rest.split("/", 1)
            if not AlistPublicStorageURL.endswith("/"):
                raw_url = f"{AlistPublicStorageURL}/{path}"
            else:
                raw_url = f"{AlistPublicStorageURL}{path}"
        
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
def RedirectToAlistRawUrl(filePath) -> flask.Response:
    """获取视频直链地址"""
    
    if filePath in URL_CACHE:
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[filePath]['time'] < 300:
            print("\nAlist Raw URL Cache exists and is valid (less than 5 minutes)")
            return flask.redirect(URL_CACHE[filePath]['url'], code=302)
    
    raw_url, code = getAlistURL(filePath)
    
    if code == 200:
        URL_CACHE[filePath] = {
            'url': raw_url,
            'time': datetime.now().timestamp()
        }
        print("Redirected Url: " + raw_url)
        return flask.redirect(raw_url, code=302)
    else:
        return flask.Response(status=code, response=raw_url)
    

# for infuse
@app.route('/Videos/<item_id>/<filename>', methods=['GET'])
# for emby
@app.route('/emby/videos/<item_id>/<filename>', methods=['GET'])
def redirect(item_id, filename):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx
    
    apiKey = extract_api_key()
    fileInfo = GetFileInfo(item_id, flask.request.args.get('MediaSourceId'), apiKey)
    
    if fileInfo['Status'] == "Error":
        print(fileInfo['Message'])
        return flask.Response(status=500, response=fileInfo['Message'])
    
    
    print(f"\n{getCurrentTime()} - Requested Item ID: {item_id}")
    print("MediaFile Mount Path: " + fileInfo['Path'])
    
    # if checkFilePath return False：return Emby originalUrl
    if not checkFilePath(fileInfo['Path']):
        redirectUrl = f"{embyPublicURL}/preventRedirct{flask.request.full_path}"
        print("Redirected Url: " + redirectUrl)
        return flask.redirect(f"{embyPublicURL}/preventRedirct{flask.request.full_path}", code=302)
    
    alist_path = optimizeFilePath(fileInfo['Path'])
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enableCache:
        return RedirectToAlistRawUrl(alist_path)

    range_header = flask.request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        print("\nWarning: Range header is not correctly formatted.")
        print(flask.request.headers)
        return RedirectToAlistRawUrl(alist_path)
    
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    start_byte = int(bytes_range.split('-')[0])
    
    # 获取缓存15秒的文件大小， 并取整
    cacheFileSize = int(fileInfo.get('Bitrate', 52428800) / 8 * 15)
    
    if start_byte < cacheFileSize:
        getCacheStatus_exists = getCacheStatus(item_id, alist_path)
        if getCacheStatus_exists:
            
            resp_headers = {
            'Content-Type': get_content_type(fileInfo['Container']),
            'Accept-Ranges': 'bytes',
            'Content-Range': f"bytes {start_byte}-{cacheFileSize-1}/{fileInfo['Size']}",
            'Content-Length': f'{cacheFileSize}',
            'Cache-Control': 'private, no-transform, no-cache',
            'X-EmbyToAList-Cache': 'Hit' if getCacheStatus(item_id, alist_path) else 'Miss',
            }
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            print(range_header)
            return flask.Response(getCacheFile(item_id, alist_path), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(putCacheFile, item_id, alist_path, flask.request.headers, cacheFileSize)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return RedirectToAlistRawUrl(alist_path)
     
    # 当请求文件末尾章节信息时
    elif fileInfo['Size'] - start_byte < 2 * 1024 * 1024:
        if getCacheStatus(item_id, path=alist_path, name=start_byte):
            resp_headers = {
            'Content-Type': get_content_type(fileInfo['Container']),
            'Accept-Ranges': 'bytes',
            'Content-Range': f"bytes {start_byte}-{fileInfo['Size']-1}/{fileInfo['Size']}",
            'Content-Length': f'{fileInfo["Size"]-start_byte}',
            'Cache-Control': 'private, no-transform, no-cache',
            'X-EmbyToAList-Cache': 'Hit' if getCacheStatus(item_id, path=alist_path, name=start_byte) else 'Miss',
            }
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            print(range_header)
            return flask.Response(getCacheFile(item_id=item_id, path=alist_path, name=start_byte), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(putCacheFile, item_id, alist_path, flask.request.headers, fileInfo['Size'], start_byte)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return RedirectToAlistRawUrl(alist_path)
    else:
        print(range_header)
        return RedirectToAlistRawUrl(alist_path)


if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')