# 简介

本项目适用于 Emby+云盘 的用法，通过 Nginx 将视频播放连接重定向到云盘的直链，从而实现 播放加速/节省流量 的目的。

具体实现灵感来自于 [MisakaFxxk/Go_stream](https://github.com/MisakaFxxk/Go_stream) 和 [bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl)。

[bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl) 项目是目前主流的劫持直链的做法，但默认会使用到 Nginx 的 njs 模块进行操作，而默认的 Nginx 发行版不包含 njs 模块。无论是动态加载，还是重新编译都较为麻烦，所以借鉴了 MisakaFxxk 的逻辑，通过 Nginx 将对应的播放请求移交给 Python 处理，最后由 Python 根据文件信息进行响应。

仅在 Emby 4.8.3 上测试成功，支持 Infuse 播放，更低或更高的 Emby 版本可能会不兼容。

# 原理解释

通过 Nginx 反向代理，将播放请求移交给 Python 处理，通过 Alist 获取到云盘直链，最后将 Emby 播放链接重定向到云盘。

由于通过 Alist 获取直链，所以所有 Alist 支持的存储后端都可以使用。

劫持的 Emby 路径为 `/emby/videos/12345/original.mp4?xxx=xxx`，通过 Python 根据 URL 中的参数查找挂载路径，然后通过 Alist api 获取文件直链后返回状态码302重定向到直链。

**缓存逻辑解释**

如果在配置文件中启用缓存，会在第一次播放云盘链接的时候缓存该视频文件的前15秒钟（通过码率计算得出），并在下次播放的时候先行返回本地的缓存文件，用于减少刚开始播放时的缓冲时间。**该功能的加速效果可能因不同的播放器的播放逻辑而产生少许变化。**

假设一个电影的码率为 30Mbps，前 15s 的大小大约为 50MB；而对于普通的一集番剧，前 15s 的大小大约只有几M。此功能可根据本地存储空间的大小自行决定是否开启。

**AlistPublicStorageURL 和 embyPublicURL**

这两项配置用于自定义播放链接的主机名。比如 Alist 中的 Onedrive 存储默认返回直链为 `https://xxx.sharepoint.com/xxx/xxx`, 假设配置`AlistPublicStorageURL` 为`https://stream.example.com/alist`，真正返回客户端的将是 `https://stream.example.com/alist/xxx/xxx`，适用于对直链反代进行加速的情景。

而 `embyPublicURL` 主要应用于对本地媒体文件的直链，该配置项只会影响到返回的文件直链，所以可以配置为 Emby 的公网域名，也可以配置为另外对 Emby 反向代理后只用于播放的域名。如果该配置与 Emby 公网域名不一样，可以实现类似于前后端分离的效果。

**Rclone 对特殊字符的处理**

在 Rclone 文档中提到，会对特殊文件名中的特殊字符串进行处理，防止由于后端存储不兼容产生的问题。在 Rclone 中，该配置是默认开启的，会导致在特殊文件名中添加一个额外的符号，比如：**"名侦探柯南：万圣节的新娘 (2022)"** 会被处理为 **"名侦探柯南‛：万圣节的新娘 (2022)"**，但在挂载路径中文件名依旧保持不变。然而 Alist 并不会对这些文件名进行处理，就会导致本地路径中的 **"/movie/名侦探柯南：万圣节的新娘 (2022)"** 而在 Alist 中为 **"/movie/名侦探柯南‛：万圣节的新娘 (2022)"**，就会导致找不到目标文件而返回404。

本程序对这种请求简单进行了处理，只需将 `convertSpecialChars` 设置为 True 后，程序会尝试将额外的符号 '**‛**' 移除。但该功能并不总是有效。

更加彻底有效的处理方法是告诉 Rclone 不要对这些特殊字符进行处理，只需在 Rclone 配置文件中对应存储的末尾添加上 `encoding = None` 即可。随后手动将存储中已经被改变的文件名称删去额外添加的字符就可以完美解决此问题。

# 配置文件说明

| 配置                    |       解释说明                                                     |       示例                                             |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| embyServer          | 和Emby通讯的地址，可以是内网                                 | http://127.0.0.1:8096  |
| emby_key            | Emby 后台生成的 Api key，通常不需要填写此选项，默认将通过请求路径获取临时 Api key | xxxxxxxxxx |
| alistServer         | 用于和 alist api 通讯的地址，可以是内网                        | http://127.0.0.1:5244   |
| alist_key           | alist令牌                                                    | alist-xxx                                           |
|AlistPublicStorageURL | 如果配置了存储直链的反向代理，可以在此配置。效果等同 Alist 中的 “自定义HOST”，唯一区别是这里可以配置二级目录反代。不需要可留空 | https://stream.example.com/alist |
| embyPublicURL | Emby 公网地址，用于路径黑名单中返回 Emby 原始播放链接。<br />可以使用和主 Emby 界面不同的域名，也可以使用一样的。程序会默认在此域名后添加额外的 “/preventRedirct” 路径防止无限重定向。 | https://stream-emby.example.com/ |
| notRedirectPaths    | 路径黑名单，如果媒体文件位置在此路径中将不会通过 alist 返回云盘直链，默认返回由 embyPublicDomain 组成的直链    | ['/mnt/local/hard-link/'] |
| convertSpecialChars | 由于rclone+OneDrive上传文件会使特殊字符后面多一个'‛'，导致无法在alist查找到文件。如果遇到这种情况，请设置为True（这里处理并不完善，请在 Rclone 配置文件中添加 encoding = None 防止 Rclone 对特殊字符进行转换） | False |
| specialCharsList    | 出现问题的特殊字符，如果遇到了其他特殊字符导致alist找不到文件可以填到这里 | ['？','：'] |
| convertMountPath    | 是否需要对路径进行修改，如果Emby挂载路径和Alist中的路径不匹配，请设置为True。程序会将该前缀从挂载路径上删去 | False |
| mountPathPrefix     | 需要删除的路径前缀                                           | /                                          |
| enableCache         | 是否启用本地缓存。<br />启用后将在播放新视频的时候缓存文件的前15秒到本地路径，适用于如Onedrive 存储的起播加速 | False |
| cachePath           | 缓存存放路径 | /path/to/Cache/ |

# 使用教程

修改配置文件后，重命名`config.example.py`到`config.py`，如果不需要WSGI服务器，可以直接使用 `python3 main.py` 启动，默认监听 `60001` 端口。使用 Flask 自带的 web 服务可能会导致一些性能问题。

仓库内同时提供了 `Gunicorn` 的配置文件，可以通过下面的命令使用 `Gunicorn` 启动
```
mkdir -p ./log

gunicorn main:app -c ./gunicorn.config.py
```

# Nginx 配置示例

程序默认监听 60001 端口

```
location ~* ^/preventRedirect/(.*)$ {
    rewrite ^/preventRedirect/(.*)$ /$1 break;
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
    proxy_cache off; 
    proxy_set_header Host $host; 
    proxy_set_header Range $http_range; 
    proxy_set_header If-Range $http_if_range; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_pass http://127.0.0.1:60001; 
}
# Proxy sockets traffic for jellyfin-mpv-shim and webClient
location ~* /(socket|embywebsocket) {
    # Proxy emby/jellyfin Websockets traffic
    proxy_pass http://127.0.0.1:8000; 
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
    tcp_nodelay on; ## Sends data as fast as it can not buffering large chunks, saves about 200ms per request.
}
location ~ / {
    proxy_pass http://127.0.0.1:8000; 
    proxy_set_header Host $host; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header REMOTE-HOST $remote_addr; 
    proxy_set_header Upgrade $http_upgrade; 
    proxy_set_header Connection "upgrade"; 
    proxy_set_header X-Forwarded-Proto $scheme; 
    proxy_http_version 1.1; 
    add_header X-Cache $upstream_cache_status; 
    add_header Strict-Transport-Security "max-age=31536000"; 
    proxy_cache off;
    proxy_cache_bypass 1;
}
```

`proxy_cache off` 和 `proxy_buffering off` 是必须的，不添加这两项会导致播放的时候出现进度乱跳的问题。
