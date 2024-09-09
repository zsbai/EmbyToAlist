import requests
import flask
from config import *
from components.utils import *
from components.cache import *
from datetime import datetime
import concurrent.futures

# 创建全局线程池
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

app = flask.Flask(__name__)

URL_CACHE = {}
    
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

# return Alist Raw Url
@get_time
def redirect_to_alist_raw_url(file_path, host_url) -> flask.Response:
    """获取视频直链地址"""
    cache_key = file_path + host_url
    if cache_key in URL_CACHE.keys():
        now_time = datetime.now().timestamp()
        if now_time - URL_CACHE[cache_key]['time'] < 300:
            print("\nAlist Raw URL Cache exists and is valid (less than 5 minutes)")
            print("Redirected Url: " + URL_CACHE[cache_key]['url'])
            return flask.redirect(URL_CACHE[cache_key]['url'], code=302)
        else:
            print("\nAlist Raw URL Cache is expired, re-fetching...")
            del URL_CACHE[cache_key]
    
    raw_url, code = get_alist_raw_url(file_path, host_url=host_url)
    
    if code == 200:
        URL_CACHE[cache_key] = {
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
    
    api_key = extract_api_key(flask)
    file_info = get_file_info(item_id, flask.request.args.get('MediaSourceId'), api_key)
    host_url = flask.request.url_root
    
    if file_info['Status'] == "Error":
        print(file_info['Message'])
        return flask.Response(status=500, response=file_info['Message'])
    
    
    print(f"\n{get_current_time()} - Requested Item ID: {item_id}")
    print("MediaFile Mount Path: " + file_info['Path'])
    
    # if checkFilePath return False：return Emby originalUrl
    if not should_redirect_to_alist(file_info['Path']):
        redirected_url = f"{host_url}preventRedirect{flask.request.full_path}"
        print("Redirected Url: " + redirected_url)
        return flask.redirect(redirected_url, code=302)
    
    alist_path = transform_file_path(file_info['Path'])
    
    # 如果没有启用缓存，直接返回Alist Raw Url
    if not enable_cache:
        return redirect_to_alist_raw_url(alist_path, host_url)

    range_header = flask.request.headers.get('Range', '')
    if not range_header.startswith('bytes='):
        print("\nWarning: Range header is not correctly formatted.")
        print(flask.request.headers)
        return redirect_to_alist_raw_url(alist_path, host_url)
    
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
    
    # 应该走缓存的情况1：请求文件开头
    if start_byte < cacheFileSize:
        
        # 判断客户端是否在黑名单中
        if any(user_agent.lower() in flask.request.headers.get('User-Agent', '').lower() for user_agent in cache_client_blacklist):
                print("Cache is disabled for this client")
                return redirect_to_alist_raw_url(alist_path, host_url)

        # 响应头中的end byte
        resp_end_byte = cacheFileSize - 1
        resp_file_size = cacheFileSize

        
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
            print("Request Range Header: " + range_header)
            print("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}")
            print("Response Content-Length: " + f'{resp_file_size}')
            return flask.Response(read_cache_file(item_id, alist_path, start_byte, cacheFileSize), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(write_cache_file, item_id, alist_path, flask.request.headers, cacheFileSize, start_byte, file_size=None, host_url=host_url)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return redirect_to_alist_raw_url(alist_path, host_url)
     
    # 应该走缓存的情况2：请求文件末尾
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
            print("Request Range Header: " + range_header)
            print("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}")
            print("Response Content-Length: " + f'{resp_file_size}')
            return flask.Response(read_cache_file(item_id=item_id, path=alist_path, start_point=start_byte, end_point=end_byte), headers=resp_headers, status=206)
        else:
            # 启动线程缓存文件
            future = executor.submit(write_cache_file, item_id, alist_path, flask.request.headers, 0, start_byte, file_info['Size'], host_url)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return redirect_to_alist_raw_url(alist_path, host_url)
    # 应该走缓存的情况3：缓存文件存在
    elif get_cache_status(item_id, path=alist_path, start_point=start_byte):
        resp_end_byte = 20 * 1024 * 1024 + start_byte - 1
        resp_file_size = 20 * 1024 * 1024

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
        print("Request Range Header: " + range_header)
        print("Response Range Header: " + f"bytes {start_byte}-{resp_end_byte}/{file_info['Size']}")
        print("Response Content-Length: " + f'{resp_file_size}')
        return flask.Response(read_cache_file(item_id=item_id, path=alist_path, start_point=start_byte, end_point=end_byte), headers=resp_headers, status=206)
    
    else:
        print("Request Range is not in cache range, redirect to Alist Raw Url")
        print("Request Range Header: " + range_header)
        return redirect_to_alist_raw_url(alist_path, host_url)


@app.route('/emby/webhook', methods=['POST'])
def webhook():
    pass

if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')