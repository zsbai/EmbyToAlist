# config.py

emby_server = "http://127.0.0.1:8096"
emby_key = ""
alist_server = "http://127.0.0.1:5244"
alist_key = ""

# Alist api 提供的 Raw Url 的 Host 替换
# 如：Alist 路径下 /movie 为 OneDrive 存储，提供的下载 Raw Url 为 xxx.sharepoint.com, 
# 经过配置后将替换为 https://download.example.com/onedrive/
# 不清楚请留空
# Example: https://download.example.com/onedrive/

# 可以用{host_url}代指请求头的host
# 如果url为列表，则自动选择二级域名一致的url。和host_url互斥
alist_download_url_replacement_map = {
    "path in Alist": "url",
    "/movie": "https://download.example.com/onedrive/",
    "/anime": "{host_url}/anime/",
    "/tv": ["https://download.example.com/tv/", "https://download.example2.net/tv/"],
}

not_redirect_paths = ['/mnt/localpath/']

# If you store your media files on OneDrive and use rclone for processing them (uploading and mounting on the server),
# it's recommended to set convertSpecialChars to True.
# This is because rclone converts certain special characters during the upload process, but displays the original characters when the files are mounted.
convert_special_chars = False
special_chars_list = ['？','：']

# this configuration is for the situation that the mount path is not the same as the alist path
# for example, in the emby server, the video path is /mnt/movie/xxx, but in the alist server, the video path is /movie/xxx
# if you 100% sure that the mount path is the same as the alist path, you can ignore this configuration
convert_mount_path = False
mount_path_prefix = "/"

# 是否缓存视频前15秒用于播放加速
enable_cache = False
cache_path = "/root/EmbyToAlist/cache"
cache_client_blacklist = ['vlc', 'infuse']