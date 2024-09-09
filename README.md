# EmbyToAlist

Rclone，Alist，Python和Nginx组合实现的Emby直链播放项目，同时配合本地缓存实现起播加速。具体逻辑为：由Rclone挂载负责Emby扫库，Alist负责提供文件直链，Python+Nginx将播放链接重定向到文件直链实现播放。

项目设计之初考虑了 本地媒体文件 和 云盘媒体文件共存 的情况，通过设置配置文件可实现仅云盘使用直链。

在Emby 4.8.3服务端上测试通过，支持Infuse，支持任何Alist可以配置的云盘，支持 本地媒体+云盘媒体 混搭，暂不支持 Strm。

灵感来自于 [MisakaFxxk/Go_stream](https://github.com/MisakaFxxk/Go_stream) 和 [bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl)，十分感谢两位大佬的开源项目。

## 部署

重命名`config.example.py`至`config.py`，修改相关配置参数。

该项目实际只由`main.py`一个文件组成，通过`config.py`配置选项，如果不想使用WSGI服务器，可直接通过`python3 main.py`启动。程序默认监听`60001`端口，使用 Flask 自带的 web 服务可能会导致一些性能问题。

仓库内同时提供了 `Gunicorn` 的配置文件，可以通过下面的命令使用 `Gunicorn` 启动服务：
```
mkdir -p ./log

gunicorn main:app -c ./gunicorn.config.py
```

启动服务器后，需要配置Nginx，将播放路径反响代理到本地`60001`端口，如果有使用 本地文件+云盘文件 的需求，则需要设置一个新的反向代理路径以防止程序陷入无限自我重定向的循环，以下是相关的Nginx配置文件示例：

```
location ~* ^/preventRedirect(/emby)?/videos/(\d*)/(stream|original).* {
    rewrite ^/preventRedirct/(.*)$ /$1 break; 

    proxy_pass http://127.0.0.1:8000; 
    #proxy_ignore_headers X-Accel-Expires Expires Cache-Control;
    #proxy_set_header Range $slice_range;
    proxy_set_header Host $host; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header Upgrade $http_upgrade; 
    proxy_set_header Connection ""; 
    add_header Strict-Transport-Security "max-age=31536000"; 
    proxy_http_version 1.1;

    add_header Cache-Control no-cache;
    proxy_cache off;
}
# /emby/Videos/12345/xxx/Subtitles/3/0/Stream.ass?api_key=xx
location ~* /videos/(\d*)/(stream|original).* {
    proxy_cache off;
    proxy_buffering off;
    proxy_set_header Host $host; 
    proxy_set_header Range $http_range; 
    proxy_set_header If-Range $http_if_range; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:60001; 
}
```

**注意：为确保请求头中的`Range`不丢失，确保中间的任何代理服务关闭缓存，如Cloudflare等。启用缓存会导致`Range`请求头丢失，从而使本地的缓存功能失效。**

## 配置文件说明

示例配置文件为根目录下的`config.example.py`

| 配置                  | 说明                                                         |
| --------------------- | ------------------------------------------------------------ |
| embyServer            | Emby服务器地址                                               |
| emby_key              | Emby Api密钥（非必需）                                       |
| alistServer           | Alist服务器地址                                              |
| alist_key             | Alist Api密钥（必需）                                        |
| AlistPublicStorageURL | 反代后端存储的主机名，也可以直接在 Alist 中配置，二选一      |
| embyPublicURL         | 本地媒体文件所使用的地址，没有特殊需要请填写Emby公网域名     |
| notRedirectPaths      | 列表，本地媒体文件存储的路径，当媒体文件在该配置中的路径下，则不会使用云盘直链 |
| convertSpecialChars   | 布尔值，设计之初为解决Rclone搭配Onedrive出现的字符问题，现不再建议使用 |
| specialCharsList      | 列表，设计之初为Rclone搭配Onedrive导致出现问题的字符，现不再建议使用 |
| convertMountPath      | 布尔值，为应对Alist路径和Rclone挂载路径不一致的问题<br />举个例子，假设Rclone挂载路径为`/mnt/movie/xxx`，Alist配置路径为`/movie/xxx`，则填写`mountPathPrefix` 为`/mnt` |
| mountPathPrefix       | 需要移除的路径前缀                                           |
| enableCache           | 是否启用本地缓存                                             |
| cachePath             | 缓存存放路径                                                 |
| cacheClientBlacklist  | 不完全支持的客户端UA                                         |

## 缓存逻辑解释

如果在配置文件中启用缓存，会在第一次播放云盘视频的时候缓存该视频文件的前15秒钟（通过码率计算得出），并在下次播放的时候先行返回本地的缓存文件，用于减少刚开始播放时的缓冲时间。**该功能的加速效果可能因不同的播放器的播放逻辑而产生少许变化。**

假设一个电影的码率为 30Mbps，前 15s 的大小大约为 50MB；而对于普通的一集番剧，前 15s 的大小大约只有几M。此功能可根据本地存储空间的大小自行决定是否开启。

已知问题：并不是所有播放器支持该缓存逻辑，在面对不支持的播放器时，需要将播放器关键词UA填入配置文件中。如果播放器不支持缓存的情况下启用缓存，会出现播放器播放完缓存的内容后认为视频已经结束，直接退出并标记该视频“已播放完成”。

截至目前，发现 “VLC 播放器 （如 Fileball 免费版）“ 和 ” Infuse 播放器” 不支持该缓存逻辑。

## Rclone搭配Onedrive存储时，对文件名称中特殊字符的处理

注：目前不再建议使用该项目解决 Rclone 特殊字符的问题，更加彻底有效的处理方法是告诉 Rclone 不要对这些特殊字符进行处理，只需在 Rclone 配置文件中对应存储的末尾添加上 `encoding = None` 即可。如果之前已经有被更改的文件名，需要手动将存储中文件名称恢复

在 Rclone 文档中提到，会对特殊文件名中的特殊字符串进行处理，防止由于后端存储不兼容产生的问题。在 Rclone 中，该配置是默认开启的，会导致在特殊文件名中添加一个额外的符号，比如：**"名侦探柯南：万圣节的新娘 (2022)"** 会被处理为 **"名侦探柯南‛：万圣节的新娘 (2022)"**，但在挂载路径中文件名依旧保持不变。然而 Alist 并不会对这些文件名进行处理，就会导致本地路径中的 **"/movie/名侦探柯南：万圣节的新娘 (2022)"** 而在 Alist 中为 **"/movie/名侦探柯南‛：万圣节的新娘 (2022)"**，从而导致文件路径不一致而返回404。

本程序对这种请求简单进行了处理，只需将 `convertSpecialChars` 设置为 True 后，程序会尝试将额外的符号 '**‛**' 移除。但该功能并不总是有效。

## **AlistPublicStorageURL 和 embyPublicURL** 配置存在的作用

该两项配置可用于间接实现Emby的前后端分离，前者为播放云盘时使用的网络域名，后者为播放本地文件时使用的网络域名，如果均不是Emby的公网地址，则可以实现前端和后端使用不同的域名，可相应配置不同的缓存策略。

## 相比于 [bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl)有什么区别？

[bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl) 项目是目前主流的劫持直链的做法，但默认会使用到 Nginx 的 njs 模块进行操作，而默认的 Nginx 发行版并不包含 njs 模块，为了不影响原有的网站配置，无论是动态加载，还是重新编译安装，都较为耗时耗力。

该EmbyToAlist由Python编写，原理为通过python启动一个本地的服务器，通过Nginx反向代理Emby播放链接到该本地服务器，通过python向Emby获取媒体详细信息，通过Alist获取文件直链，最后返回302重定向至云盘直链。

由于Python的拓展性较强，所以尝试性的加入了文件缓存功能。该功能本意为减少视频播放器的加载时间，通过第一次播放时缓存视频文件的前几秒到本地磁盘，在后续请求时优先返回缓存文件来解决播放开始时加载时间较长的问题。但由于实现方法略微抽象，所以不通播放器所获得的加速效果有限，当前测试不完全支持的播放器有：VLC，Infuse。

该缓存效果在面对云盘存储直链访问延迟较高的情况下效果较好。

通过测试 Fileball 的 **mpv** 播放器，在使用Onedrive作为后端存储的情况下，可以实现在有缓存的状态下秒开。

## 完整 Nginx 反向代理配置示例

该配置并没有缓存任何静态文件，因为我使用Cloudflare缓存全站，后续会给出相关配置

```
location ~* ^/preventRedirect(/emby)?/videos/(\d*)/(stream|original).* {
    rewrite ^/preventRedirect/(.*)$ /$1 break; 

    proxy_pass http://127.0.0.1:8000; 
    #proxy_ignore_headers X-Accel-Expires Expires Cache-Control;
    #proxy_set_header Range $slice_range;
    proxy_set_header Host $host; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header Upgrade $http_upgrade; 
    proxy_set_header Connection ""; 
    add_header Strict-Transport-Security "max-age=31536000"; 
    proxy_http_version 1.1;

    add_header Cache-Control no-cache;
    proxy_cache off;

    #slice 16m;
    #proxy_cache emby-videos;
    #proxy_cache_lock on;
    #proxy_cache_lock_age 5s;

    #proxy_cache_key $uri$is_args$args$slice_range;
    #proxy_cache_valid 200 206 7d;
}
# /emby/Videos/12345/xxx/Subtitles/3/0/Stream.ass?api_key=xx
location ~* /videos/(\d*)/(stream|original).* {
    proxy_cache off;
    proxy_buffering off;
    proxy_set_header Host $host; 
    proxy_set_header Range $http_range; 
    proxy_set_header If-Range $http_if_range; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header X-Forwarded-Proto $scheme;
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
}
```

## Cloudflare Cache Rule 示例配置

使用 Cloudflare Cache Rule 缓存 Emby 全站，排除API等关键配置，思路来自于[Nolovenodie](https://github.com/Nolovenodie)/[emby-direct](https://github.com/Nolovenodie/emby-direct)项目

在Cloudflare控制台打开Cache-Rule，缓存规则主要需要满足三个要点，绕过api，视频，除此之外缓存全部内容；除此之外，顺序靠后的规则会覆盖掉靠前的规则，所以缓存全部内容的规则顺序需要放在第一位：

1. 缓存全站

<img width="818" alt="图片" src="https://github.com/zsbai/EmbyToAlist/assets/62942942/37873591-9ffe-40f7-bc67-83c69855133a">

2. 缓存排除视频文件

<img width="816" alt="图片" src="https://github.com/zsbai/EmbyToAlist/assets/62942942/bb7b094a-877a-4a50-ab0a-8dad3bf90926">

缓存策略设置为 `Bypass`

3. 排除所有 API 相关的请求

<img width="842" alt="图片" src="https://github.com/zsbai/EmbyToAlist/assets/62942942/cba98bc0-5c12-457c-a290-6a740fa81b7c">

缓存策略设置为`Bypass`


缓存策略根据自己的喜欢设置，作为参考，我设置的 Edge TTL（在Cloudflare边缘节点上缓存时间）为6个月



