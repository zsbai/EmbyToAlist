# config.py

embyServer = "http://127.0.0.1:8096"
embyPublicDomain = "https://emby.example.com"
emby_key = ""
alistServer = "http://127.0.0.1:5244"
alist_key = ""
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

cachePath = "/root/EmbyToAlist/cache"