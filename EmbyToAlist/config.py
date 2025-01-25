from environs import Env

env = Env()
env.read_env()

EMBY_SERVER = env.str("EMBY_SERVER")

ALIST_SERVER = env.str("ALIST_SERVER")
ALIST_API_KEY = env.str("ALIST_API_KEY")

IGNORE_PATH = env.list("IGNORE_PATH", subcast=str, default=[])

MOUNT_PATH_PREFIX_REMOVE = env.str("MOUNT_PATH_PREFIX_REMOVE", default="")
MOUNT_PATH_PREFIX_ADD = env.str("MOUNT_PATH_PREFIX_ADD", default="")

CACHE_ENABLE = env.bool("CACHE_ENABLE", default=False)
CACHE_NEXT_EPISODE = env.bool("CACHE_NEXT_EPISODE", default=False)
CACHE_PATH = env.str("CACHE_PATH", default="./cache")
FORCE_CLIENT_RECONNECT = env.bool("FORCE_CLIENT_RECONNECT", default=False)

LOG_LEVEL = env.str("LOG_LEVEL", default="info").lower()