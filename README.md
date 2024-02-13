# 简介

本仓库适用于 Emby+云盘 的玩法，通过 Nginx 将视频播放连接重定向到云盘的直链，从而实现 播放加速/节省流量 的目的。

具体实现灵感来自于 [MisakaFxxk/Go_stream](https://github.com/MisakaFxxk/Go_stream) 和 [bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl)。

由于默认的 Nginx 发行版是不包含njs的，重新编译安装又较为麻烦，所以借鉴了 MisakaFxxk 的做法，通过Nginx将播放路径反向代理，再通过 Python 实现重定向的操作

仅在 Emby 4.8.0.76beta 上测试成功，更低的版本可能会不兼容。

# 原理解释

通过 Nginx 反向代理，将 Emby 的播放链接进行处理，通过 Alist 获取到云盘直链，最后将 Emby 播放链接重定向到云盘。

劫持路径为 /emby/videos/12345/original.mp4?xxx=xxx 的请求，通过python根据URL中的参数查找挂载路径，然后通过 Alist api 获取文件直链后返回状态码302重定向到直链。

# 配置文件

| 配置                    |       解释                                                       |
| ------------------- | ------------------------------------------------------------ |
| embyServer          | 和Emby通讯的地址，可以是内网                                 |
| embyPublicDomain    | Emby公网地址，用于路径黑名单中返回Emby原始播放链接           |
| emby_key            | 一般情况下是不需要填此项的，某些特殊情况下Emby播放链接中没有api_key，需要后台生成api_key才能获取到媒体路径 |
| alistServer         | 用于和alist api通讯的地址，可以是内网                        |
| alist_key           | alist令牌                                                    |
| notRedirectPaths    | 路径黑名单，如果媒体在此路径中将不会通过alist返回云盘直链    |
| convertSpecialChars | 由于rclone+OneDrive上传文件会使特殊字符后面多一个'‛'，导致无法在alist查找到文件。如果遇到这种情况，请设置为True |
| specialCharsList    | 出现问题的特殊字符，如果遇到了其他特殊字符导致alist找不到文件可以填到这里 |
| convertMountPath    | 是否需要对路径进行修改，如果Emby挂载路径和Alist中的路径不匹配，请设置为True。程序会将该前缀从挂载路径上删去 |
| mountPathPrefix     | 需要删除的路径前缀                                           |

# 使用教程

修改配置文件后，重命名`config.example.py`到`config.py`，如果不需要WSGI服务器，可以直接使用 `python main.py` 启动，默认监听 `60001` 端口。

仓库内同时提供了Gunicorn的配置文件，可以通过下面的命令使用 Gunicorn 启动
```
mkdir -p ./log

gunicorn main:app -c ./gunicorn.config.py
```

# Nginx配置示例

程序默认监听 60001 端口

```
location ~* ^/preventRedirct/(.*)$ {
    rewrite ^/preventRedirct/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:8096;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header REMOTE-HOST $remote_addr;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_cache off;
    proxy_buffering off;
}

# /emby/Videos/12345/xxx/Subtitles/3/0/Stream.ass?api_key=xx
location ~* /videos/(\d*)/(stream|original).* {
    # Cache alist direct link
    add_header Cache-Control max-age=3600; 
    proxy_pass http://127.0.0.1:60001; 
}
# Proxy sockets traffic for jellyfin-mpv-shim and webClient
location ~* /(socket|embywebsocket) {
    # Proxy emby/jellyfin Websockets traffic
    proxy_pass http://127.0.0.1:8096;
    ## WEBSOCKET SETTINGS ## Used to pass two way real time info to and from emby and the client.
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Protocol $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_connect_timeout 1h;
    proxy_send_timeout 1h;
    proxy_read_timeout 1h;
    tcp_nodelay on;  ## Sends data as fast as it can not buffering large chunks, saves about 200ms per request.
}
location ~ / {
    proxy_pass http://127.0.0.1:8096; 
    proxy_set_header Host $host; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header REMOTE-HOST $remote_addr; 
    proxy_set_header Upgrade $http_upgrade; 
    proxy_set_header Connection "upgrade"; 
    proxy_set_header X-Forwarded-Proto $scheme; 
    proxy_http_version 1.1; 
    add_header X-Cache $upstream_cache_status; 
    #if ($request_uri ~* ^/emby/videos/\d*/(original|stream)) {
    #proxy_pass http://127.0.0.1:60001;
    #} 
    add_header Strict-Transport-Security "max-age=31536000"; 
    add_header Cache-Control no-cache; 
    proxy_buffering off;
}
```

关于第二个 location 块，由于路径黑名单里的媒体文件会重定向到原始链接，但原始链接就又会被nginx反向代理到python，然后无限循环...

暂时没有想到更好的方法，只能添加了一个 /preventRedirect 的路径然后单独处理。

`proxy_cache off` 和 `proxy_buffering off` 是必须的，不添加这两项会导致播放的时候出现进度乱跳的问题。
