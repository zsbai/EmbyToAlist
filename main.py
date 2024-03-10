import requests
import flask
import re
from config import *
import os

app = flask.Flask(__name__)


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

# used to get the file info from emby server
def GetFileInfo(item_id, MediaSourceId, apiKey) -> dict:
    data = {}
    url = f"{embyServer}/emby/Items/{item_id}/PlaybackInfo?MediaSourceId={MediaSourceId}&api_key={apiKey}"
    print(url)
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
    # print(f"Path: {filePath} is not in notRedirectPaths, return Alist Raw Url")
    return True


def putCacheFile(item_id, url, headers, size=52428800) -> bool:
    """缓存文件"""
    print(f"\nStart to cache file: {item_id}")
    if os.path.exists(os.path.join(cachePath, item_id)):
        print("WARNING: Cache File Already Exists or Cache File is being written. Abort.")
        return False
    headers = dict(headers) # Copy the headers
    headers['Host'] = url.split('/')[2]
    # Check if Range in header
    if 'Range' in headers:
        if headers['Range'].startswith('bytes=0-'):
            # Modify the range to 0-first50M
            headers['Range'] = f"bytes=0-{size-1}"
        else:
            print(headers)
            return False
    else:
        print("Cache Error: No Range in headers")
        return False
    
    resp = requests.get(url, headers=headers, stream=True)
    if resp.status_code == 206:
        if not os.path.exists(cachePath):
            os.makedirs(cachePath)
        print(f"Start to write cache file: {item_id}")
        with open (os.path.join(cachePath, item_id), 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024):
                f.write(chunk)
        print(f"Cache file: {item_id} has been written")
        return True
    else:
        print(f"Cache Error: Upstream return code: {resp.status_code}")
        return False
    
def getCacheFile(item_id):
    """读取缓存文件"""
    path = os.path.join(cachePath, item_id)
    with open(f'{path}', 'rb') as f:
        data = f.read(1024 * 1024)
        while data:
            yield data
            data = f.read(1024 * 1024)

def getCacheStatus(item_id) -> bool:
    """检查缓存文件是否存在"""
    return os.path.exists(os.path.join(cachePath, item_id))

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
def GetRedirectUrl(filePath):
    """获取视频直链地址"""
    # if checkFilePath return False：return Emby originalUrl
    if not checkFilePath(filePath):
        return f"{embyPublicDomain}/preventRedirct{flask.request.full_path}"
    
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
        return req['data']['raw_url']
    elif code == 403:
        print("403 Forbidden, Please check your Alist Key")
        return 403
    elif code == 500:
        print(f"Error: {req['message']}")
        return 500
    else:
        print(f"unknow error: {req['message']}")
        return code

def handle_redirect_or_cache(redirectUrl, item_id, resp_headers, cacheFileSize):
    """处理重定向或缓存"""
    if not enableCache:
        return flask.redirect(redirectUrl, code=302)
    
    cache_status = getCacheStatus(item_id)
    if cache_status:
        if not flask.request.headers.get('Range').startswith('bytes=0-'):
            return flask.redirect(redirectUrl, code=302)
        else:
            print("\nCache File Found")
            return flask.Response(getCacheFile(item_id), headers=resp_headers, status=206)
    else:
        status = putCacheFile(item_id, redirectUrl, flask.request.headers, cacheFileSize)
        if not status:
            print("Cache Error: Can't Cache File")
            return flask.redirect(redirectUrl, code=302)
        else:
            # 缓存成功，直接返回缓存文件节省缓冲时间
            return flask.Response(getCacheFile(item_id), headers=resp_headers, status=206)
     
 
 
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
    
    # 缓存15秒， 并取整
    cacheFileSize = int(fileInfo.get('Bitrate', 52428800) / 8 * 15)
        
    print("MediaFile Mount Path: " + fileInfo['Path'])
    redirectUrl = GetRedirectUrl(optimizeFilePath(fileInfo['Path']))
    
    resp_headers = {
        'Content-Type': 'video/x-matroska',
        'Accept-Ranges': 'bytes',
        'Content-Range': f'bytes 0-{cacheFileSize-1}/{fileInfo['Size']}',
        'Content-Length': f'{cacheFileSize}',
        'Cache-Control': 'private, no-transform',
        }
    
    if isinstance(redirectUrl, int):
        return flask.Response(status=redirectUrl)
    elif redirectUrl.startswith(embyPublicDomain):
        return flask.redirect(redirectUrl, code=302)
    
    return handle_redirect_or_cache(redirectUrl, item_id, resp_headers, cacheFileSize)

if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')