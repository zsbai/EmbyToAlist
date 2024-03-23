# config.py

embyServer = "http://127.0.0.1:8096"
embyPublicDomain = "https://emby.example.com"
emby_key = ""
alistServer = "http://127.0.0.1:5244"
alist_key = ""

# 反代后端存储的主机名，也可以直接在 Alist 中配置，二选一
# 由于 Alist 不支持二级目录的反代地址，所以如果反向代理地址是二级目录，需要在这里配置
# Example: https://api.example.com/onedrive/
ReverseStorageUrl = ""

notRedirectPaths = ['/mnt/localpath/']

# If you store your media files on OneDrive and use rclone for processing them (uploading and mounting on the server),
# it's recommended to set convertSpecialChars to True.
# This is because rclone converts certain special characters during the upload process, but displays the original characters when the files are mounted.
convertSpecialChars = False
specialCharsList = ['？','：']

# this configuration is for the situation that the mount path is not the same as the alist path
# for example, in the emby server, the video path is /mnt/movie/xxx, but in the alist server, the video path is /movie/xxx
# if you 100% sure that the mount path is the same as the alist path, you can ignore this configuration
convertMountPath = False
mountPathPrefix = "/"

# 是否缓存视频前15秒用于播放加速
enableCache = False
cachePath = "/root/EmbyToAlist/cache"