# EmbyToAlist

通过 Nginx 反向代理 + Python FastAPI + Alist 实现的 Emby 播放302重定向项目, 支持STRM。

灵感来自于 [MisakaFxxk/Go_stream](https://github.com/MisakaFxxk/Go_stream) ， [bpking1/embyExternalUrl](https://github.com/bpking1/embyExternalUrl)和[Nolovenodie](https://github.com/Nolovenodie)/[emby-direct](https://github.com/Nolovenodie/emby-direct)。

* 更新：已通过 FastAPI 重构，相比于 Flask，响应速度有较大提升。

* 更新： 更新至V2版本，增加对STRM的支持，配置项更改为环境变量。从V1升级到V2需要清空 Cache 文件夹。

# 用途

1. 关闭缓存

* 播放时重定向到文件直链（不消耗本机流量）
* 支持 STRM
* 少少许加快起播速度

2. 花费少量本地空间开启视频开头缓存

* 通过本机反向代理后端存储（需消耗本机流量）
* 无缓存时302重定向到文件直链
* 初次播放后缓存视频文件开头元数据
* 大幅加快起播速度
* 降低播放器开始播放时对后端的请求并发数量
* 播放剧集时自动缓存下一集

# 部署方式

## 1. 直接部署

```
# 克隆项目到本地
$ git clone git@github.com:zsbai/EmbyToAlist.git && cd EmbyToAlist
# 安装依赖
$ pip install -r requirements.txt
# 修改配置文件
$ cp .env.example .env
# 运行 EmbyToAlist
$ python3 -m EmbyToAlist
```

## 2. 通过docker compose部署
```
# 获取docker-compose.yml文件 和 配置文件
$ wget https://raw.githubusercontent.com/zsbai/EmbyToAlist/refs/heads/main/docker-compose.yml -O docker-compose.yml && wget https://raw.githubusercontent.com/zsbai/EmbyToAlist/refs/heads/main/.env.example -O .env
# 配置 docker-compose.yml 中的路径网络
# 修改 .env 文件
# 启动
$ docker compose up -d
# 更新
$ docker compose pull && docker compose up -d
# 删除
$ docker compose down
# 查看日志
$ docker compose logs
```

示例 docker-compose.yml 文件：
```
services:
  embytoalist:
    image: ghcr.io/zsbai/embytoalist:latest
    volumes:
      - ./config.py:/app/config.py
      - /path/to/cache_dir:/path/to/cache_dir
    # host和port二选一，设置为host可以直接通过 127.0.0.1 访问宿主机上的alist和emby服务
    # 设置为port需要使用docker网关访问宿主机
    network_mode: host
    # ports:
    #   - 127.0.0.1:60001:60001
    restart: unless-stopped
```

启动服务器后，需要配置 Nginx，将播放路径反向代理到本地`60001`端口。

**为确保请求头中的`Range`不丢失，确保中间的任何代理服务都不会缓存视频文件，如Cloudflare等。启用缓存视频文件会导致`Range`请求头丢失，从而使本地的缓存功能失效。末尾有 Cloudflare Cache Rule 示例**

如果有使用 **本地文件+云盘文件** 的需求，则需要设置一个新的反向代理路径以防止程序陷入无限自我重定向的循环，以下是相关的Nginx配置文件示例：

```
  set $emby "http://127.0.0.1:8096";

  # reverse proxy
  location ~* ^/preventRedirect/(emby/)?videos/(\d*)/(stream|original).* {
      rewrite ^/preventRedirect/(.*)$ /$1 break;
      proxy_pass $emby;
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
      proxy_http_version 1.1;
      proxy_set_header Connection "";

      proxy_pass http://127.0.0.1:60001;
  }
  # Proxy sockets traffic for jellyfin-mpv-shim and webClient
  location ~* /(socket|embywebsocket) {
      # Proxy emby/jellyfin Websockets traffic
      proxy_pass $emby;
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
      proxy_pass $emby;
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

# 配置文件

* `EMBY_SERVER`：字符串，Emby 服务器地址（建议内网地址）
* `ALIST_SERVER`：字符串，Alist服务器地址（建议内网地址）
* `ALIST_API_KEY`：字符串，Alist 密钥 （必须）

* `IGNORE_PATH`：列表，本地文件路径开头，该路径下的媒体文件将不会通过 AList 重定向到文件直链。示例：["/local/media"]

* `MOUNT_PATH_PREFIX_REMOVE`：字符串，挂载路径需要移除的路径前缀，示例："/mnt"。不需要请留空
* `MOUNT_PATH_PREFIX_ADD`：字符串，移除后需要额外添加的路径前缀，示例：“/media”。不需要请留空

对于上面的示例配置，路径将进行如下的转换："/mnt/电影/ABC/ABC.mkv" -> "/电影/ABC/ABC.mkv" -> "/media/电影/ABC/ABC.mkv"

最后转换完成的路径应该是Alist中的路径。

* `CACHE_ENABLE`：布尔值，是否缓存媒体文件的前15秒进行加速（通过码率计算）。
* `CACHE_NEXT_EPISODE`：布尔值，在播放剧集的时候自动缓存下一集
* `CACHE_PATH`：字符串，缓存存放的路径。
实验项目：
* `FORCE_CLIENT_RECONNECT`：使用缓存的时候，响应完缓存内容后强制打断客户端连接，迫使客户端重新发起请求后响应302，以在开启缓存后同时使用302减少服务端流量消耗（注：开启后每次播放反响代理必然会报错，忽视即可）。

* `LOG_LEVEL`：字符串，日志等级。示例：“debug“。

# 项目实现方法 & 逻辑解释

## 1. 缓存逻辑解释

主流播放器在播放 Emby 中的媒体文件时，通常会发起大量请求，请求视频开头和末尾的元数据。如果后端直链响应延迟较高，将会大幅延长开始播放时所需的加载时间。

如果在配置文件中启用缓存，会在第一次播放云盘视频的时候缓存该视频文件的前15秒钟（通过码率计算得出），并在下次播放的时候先行返回本地的缓存文件，用于减少刚开始播放时的缓冲时间。**该功能的加速效果可能因不同的播放器的播放逻辑而产生少许变化。**

假设一个电影的码率为 30Mbps，前 15s 的大小大约为 50MB；而对于普通的一集番剧，前 15s 的大小大约只有几M。此功能可根据本地存储空间的大小自行决定是否开启。

当启用缓存后，程序将通过反向代理文件直链的方式，先响应本地缓存，之后请求直链，流式传输后续内容。该方式将会消耗本机流量，但是可以兼容所有主流播放器，是一种妥协的实现方法。

缓存效果：在mpv播放器上，播放存储在 Onedrive 的媒体文件，开始播放所消耗的时间：约10s -> 小于1s

## 2. Rclone搭配Onedrive存储时，对文件名称中特殊字符的处理

注：目前不再建议使用该项目解决 Rclone 特殊字符的问题，更加彻底有效的方法是告诉 Rclone 不要对这些特殊字符进行处理，只需在 Rclone 配置文件中对应存储的末尾添加上 `encoding = None` 即可。如果之前已经有被更改的文件名，需要手动将存储中文件名称恢复

在 Rclone 文档中提到，会对特殊文件名中的特殊字符串进行处理，防止由于后端存储不兼容产生的问题。在 Rclone 中，该配置是默认开启的，会导致在特殊文件名中添加一个额外的符号，比如：**"名侦探柯南：万圣节的新娘 (2022)"** 会被处理为 **"名侦探柯南‛：万圣节的新娘 (2022)"**，但在挂载路径中文件名依旧保持不变。然而 Alist 并不会对这些文件名进行处理，就会导致本地路径中的 **"/movie/名侦探柯南：万圣节的新娘 (2022)"** 而在 Alist 中为 **"/movie/名侦探柯南‛：万圣节的新娘 (2022)"**，从而导致文件路径不一致而返回404。

本程序对这种请求简单进行了处理，只需将 `convertSpecialChars` 设置为 True 后，程序会尝试将额外的符号 '*‛*' 移除。但该功能并不总是有效。

# Cloudflare Cache Rule 示例配置

使用 Cloudflare Cache Rule 缓存 Emby 全站，排除API等关键配置，思路来自于[Nolovenodie](https://github.com/Nolovenodie)/[emby-direct](https://github.com/Nolovenodie/emby-direct)项目

在Cloudflare控制台打开Cache-Rule，缓存规则主要需要满足三个要点，绕过api，视频，除此之外缓存全部内容；除此之外，顺序靠后的规则会覆盖掉靠前的规则，所以缓存全部内容的规则顺序需要放在第一位：

1. 缓存全站

<img width="818" alt="图片" src="https://github.com/zsbai/EmbyToAlist/assets/62942942/37873591-9ffe-40f7-bc67-83c69855133a">

2. 缓存排除视频文件

<img width="816" alt="图片" src="https://github.com/zsbai/EmbyToAlist/assets/62942942/bb7b094a-877a-4a50-ab0a-8dad3bf90926">

缓存策略设置为 `Bypass`

3. 排除所有 API 相关的请求

![图片](https://github.com/user-attachments/assets/b661e6ae-e7b3-4be7-9c4f-7a86b02174ca)

缓存策略设置为`Bypass`，字段均为`URI 路径`，使用 `URI` 会导致错误缓存剧集列表，导致不显示新入库剧集。

缓存策略根据自己的喜欢设置，作为参考，我设置的 Edge TTL（在Cloudflare边缘节点上缓存时间）为6个月
