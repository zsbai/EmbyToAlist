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
    
    api_key = extract_api_key(flask)
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
    
    # 应该走缓存的情况1：请求文件开头
    if start_byte < cacheFileSize:
        
        # 判断客户端是否在黑名单中
        if any(user_agent.lower() in flask.request.headers.get('User-Agent', '').lower() for user_agent in cache_client_blacklist):
                print("Cache is disabled for this client")
                return redirect_to_alist_raw_url(alist_path)

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
            future = executor.submit(write_cache_file, item_id, alist_path, flask.request.headers, cacheFileSize, start_byte)
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return redirect_to_alist_raw_url(alist_path)
     
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
            future = executor.submit(write_cache_file, item_id, alist_path, flask.request.headers, 0, start_byte, file_info['Size'])
            future.add_done_callback(lambda future: print(future.result()))

            # 重定向到原始URL
            return redirect_to_alist_raw_url(alist_path)
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
        return redirect_to_alist_raw_url(alist_path)


@app.route('/emby/webhook', methods=['POST'])
def webhook():
    """
    通过启用 Emby Webhook 可以实现以下功能：
    1. 根据用户的观看位置，根据停止位置创建和删除缓存文件
    """
    if flask.request.headers.get('Content-Type') != 'application/json':
        return flask.Response(status=415, response='Unsupported Request Type')
    
    data = flask.request.json
    Event = data.get('Event', '')
    
    if Event == "playback.start":
        # 开始播放时删除缓存文件
        PositionTicks = data.get('PlayBackInfo', {}).get('PositionTicks', 0)
        RunTimeTicks = data.get('Item', {}).get('RunTimeTicks', 0)
        Size = data.get('Item', {}).get('Size', 0)
        Path = data.get('Item', {}).get('Path', '')
        Item_Id = data.get('Item', {}).get('Id', '')
        if play_percent < 0.03 or play_percent > 0.90 or PositionTicks / 10_000_000 < 120:
            print("Webhook: No Cache")
            return flask.Response(status=200, response='No Cache')
        else:
            # 创建缓存删除标记，将在播放结束时删除缓存文件
            create_cache_delete_tag(Item_Id, transform_file_path(Path), Size * play_percent)
            return flask.Response(status=200, response='Cache Delete Tag Created')
            
    elif Event == "playback.stop":
        
        # 停止的时候创建缓存文件
        # Date = data.get('Date', '')
        # User = data.get('User', {}).get('Name', '')
        PositionTicks = data.get('PlayBackInfo', {}).get('PositionTicks', 0)
        RunTimeTicks = data.get('Item', {}).get('RunTimeTicks', 0)
        Size = data.get('Item', {}).get('Size', 0)
        Path = data.get('Item', {}).get('Path', '')
        Item_Id = data.get('Item', {}).get('Id', '')
        
        play_percent = PositionTicks / RunTimeTicks
        
        # 如果播放百分比小于3%或大于90%，或者播放时间小于120秒，则不缓存
        if play_percent < 0.03 or play_percent > 0.90 or PositionTicks / 10_000_000 < 120:
            print("Webhook: No Cache")
            return flask.Response(status=200, response='No Cache')
        else:
            # 创建缓存： 20MB， 缓存文件起始位置：播放百分比 * 文件大小
            future = executor.submit(write_cache_file, Item_Id, transform_file_path(Path), flask.request.headers, 20 * 1024 * 1024, Size * play_percent)
            future.add_done_callback(lambda future: print(future.result()))
            return flask.Response(status=200, response='Cache Created')
    # 其他事件
    else:
        pass

if __name__ == "__main__":
    app.run(port=60001, debug=True, threaded=True, host='0.0.0.0')