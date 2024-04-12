import requests
import flask
import re
from config import *
import os
from datetime import datetime
import concurrent.futures

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


def putCacheFile(item_id, url, headers, size=52428800) -> bool:
    """缓存文件"""
    print(f"\n {getCurrentTime()}-Start to cache file: {item_id}")
    cache_file_path = os.path.join(cachePath, item_id, 'cache_file')
    
    if os.path.exists(cache_file_path):
        print(f"{getCurrentTime()}-WARNING: Cache File Already Exists or Cache File is being written. Abort.")
        return False
    else:
        os.makedirs(os.path.join(cachePath, item_id), exist_ok=True)
        
    headers = dict(headers) # Copy the headers
    headers['Host'] = url.split('/')[2]
      
    # Modify the range to 0-first50M
    headers['Range'] = f"bytes=0-{size-1}"

    resp = requests.get(url, headers=headers, stream=True)
    if resp.status_code == 206: 
        # print(f"Start to write cache file: {item_id}")
        with open (cache_file_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024):
                f.write(chunk)
                
        print(f"{getCurrentTime()}-Cache file: {item_id} has been written")
        return True
    else:
        print(f"{getCurrentTime()}-Cache Error: Upstream return code: {resp.status_code}")
        return False
    
def getCacheFile(item_id):
    """读取缓存文件"""
    path = os.path.join(cachePath, item_id, 'cache_file')
    with open(f'{path}', 'rb') as f:
        data = f.read(1024 * 1024)
        while data:
            yield data
            data = f.read(1024 * 1024)

def getCacheStatus(item_id) -> tuple:
    """检查缓存文件是否存在，并检查其最后修改时间"""
    cache_file_path = os.path.join(cachePath, item_id, 'cache_file')
    
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

# return Alist Raw Url or Emby Original Url
@get_time
def RedirectToAlistRawUrl(filePath):
    """获取视频直链地址"""
    
    if filePath in URL_CACHE:
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[filePath]['time'] < 300:
            print("\nAlist Raw URL Cache exists and is valid (less than 5 minutes)")
            return flask.redirect(URL_CACHE[filePath]['url'], code=302)
    
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
        return 500
    
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
        
        URL_CACHE[filePath] = {
            'url': raw_url,
            'time': datetime.now().timestamp()
        }
        print("Redirected Url: " + raw_url)
        return flask.redirect(raw_url, code=302)
            
        
    elif code == 403:
        print("403 Forbidden, Please check your Alist Key")
        return flask.Response(status=403, response="403 Forbidden, Please check your Alist Key")
    else:
        print(f"Error: {req['message']}")
        return flask.Response(status=500, response=req['message'])

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
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enableCache:
        return RedirectToAlistRawUrl(optimizeFilePath(fileInfo['Path']))
    
    range_header = flask.request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        print("\nWarning: Range header is not correctly formatted.")
        print(flask.request.headers)
        return RedirectToAlistRawUrl(optimizeFilePath(fileInfo['Path']))
    
    # 解析Range头，获取请求的起始字节
    bytes_range = range_header.split('=')[1]
    start_byte = int(bytes_range.split('-')[0])
    
    # 获取缓存15秒的文件大小， 并取整
    cacheFileSize = int(fileInfo.get('Bitrate', 52428800) / 8 * 15)
    
    if start_byte < cacheFileSize:
        getCacheStatus_exists = getCacheStatus(item_id)
        if getCacheStatus_exists:
            
            resp_headers = {
            'Content-Type': get_content_type(fileInfo['Container']),
            'Accept-Ranges': 'bytes',
            'Content-Range': f"bytes {start_byte}-{cacheFileSize-1}/{fileInfo['Size']}",
            'Content-Length': f'{cacheFileSize}',
            'Cache-Control': 'private, no-transform, no-cache',
            'X-EmbyToAList-Cache': 'Hit' if getCacheStatus(item_id) else 'Miss',
            }
            
            print("\nCached file exists and is valid")
            # 返回缓存内容和调整后的响应头
            print(range_header)
            return flask.Response(getCacheFile(item_id), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(putCacheFile, item_id, redirectUrl, flask.request.headers, cacheFileSize)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return RedirectToAlistRawUrl(optimizeFilePath(fileInfo['Path']))
            
    else:
        print(range_header)
        return RedirectToAlistRawUrl(optimizeFilePath(fileInfo['Path']))

if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')