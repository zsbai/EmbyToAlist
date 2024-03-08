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
    for path in notRedirectPaths:
        if filePath.startswith(path):
            print(f"\nFilePath is in notRedirectPaths, return Emby Original Url")
            return False
    # print(f"Path: {filePath} is not in notRedirectPaths, return Alist Raw Url")
    return True


def putCacheFile(item_id, url, headers):
    headers = dict(headers) # Copy the headers
    # Check if Range in header
    if 'Range' in headers:
        if headers['Range'].startswith('bytes=0-'):
            # Modify the range to 0-first50M
            headers['Range'] = 'bytes=0-52428800'
        else:
            return False
    else:
        print("Error: No Range in headers")
        return False
    resp = requests.get(url, headers=headers, stream=True)
    if resp.status_code == 206:
        with open (f'{cachePath}/{item_id}', 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024):
                f.write(chunk)
        return True
    else:
        print(f"Error: {resp.status_code}")
        return False
    
def getCacheFile(item_id):
    path = os.path.join(cachePath, item_id)
    with open(f'{path}', 'rb') as f:
        data = f.read(1024 * 1024)
        while data:
            yield data
            data = f.read(1024 * 1024)

def getNewCacheHeaders(headers):
    headers = dict(headers) # Copy the headers
    # remove old content-range
    if 'Content-Range' in headers:
        del headers['Content-Range']
    elif 'content-range' in headers:
        del headers['content-range']
    
    headers['Content-Range'] = 'bytes 0-52428800/52428801'
    headers['Content-Length'] = '52428801'
    return headers

def getCacheStatus(item_id):
    return os.path.exists(os.path.join(cachePath, item_id))

# return Alist Raw Url or Emby Original Url
@get_time
def GetRedirectUrl(filePath):
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

# for infuse
@app.route('/Videos/<item_id>/<filename>', methods=['GET'])
# for emby
@app.route('/emby/videos/<item_id>/<filename>', methods=['GET'])
def redirect(item_id, filename):
    # Example: https://emby.example.com/emby/Videos/xxxxx/original.mp4?MediaSourceId=xxxxx&api_key=xxxxx

    MediaSourceId = flask.request.args.get('MediaSourceId')
    apiKey = flask.request.args.get('api_key')
    
    if not apiKey:
        # for infuse
        # try to catch the token from the header
        auth_header = flask.request.headers.get('X-Emby-Authorization')
        if auth_header:
            match_token = re.search(r'Token="([^"]+)"', auth_header)
            if match_token:
                apiKey = match_token.group(1)
        if not apiKey:
            apiKey = emby_key

    fileInfo = GetFileInfo(item_id, MediaSourceId, apiKey)
    if fileInfo['Status'] == "Error":
        print(fileInfo['Message'])
        return flask.Response(status=500, response=fileInfo['Message'])
    
    filePath = fileInfo['Path']
    
    print("MediaFile Mount Path: " + filePath)
    redirectUrl = GetRedirectUrl(optimizeFilePath(filePath))
    
    # originalUrl = f"{embyServer}{flask.request.full_path}"
    
    if type(redirectUrl) == int:
        return flask.Response(status=redirectUrl)
    elif getCacheStatus(item_id):
        print("\nCache File Found")
        headers = getNewCacheHeaders(flask.request.headers)
        return flask.Response(getCacheFile(item_id), headers=headers, status=206)
    else:
        print("\nCache File Not Found")
        putCacheFile(item_id, redirectUrl, flask.request.headers)
        print("\nRedirect to: "+ redirectUrl)
        return flask.redirect(redirectUrl, code=302)

if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')