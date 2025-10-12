from environs import Env

env = Env()
env.read_env()

EMBY_SERVER = env.str("EMBY_SERVER")

ALIST_SERVER = env.str("ALIST_SERVER")
ALIST_API_KEY = env.str("ALIST_API_KEY")
RAW_LINK_PROVIDER = env.str("RAW_LINK_PROVIDER", default="alist").strip().lower()
RAW_LINK_EMBY_SERVER = env.str("RAW_LINK_EMBY_SERVER", default="").strip()
RAW_LINK_EMBY_KEY = env.str("RAW_LINK_EMBY_KEY", default="").strip()

ENABLE_UA_PASSTHROUGH = env.bool("ENABLE_UA_PASSTHROUGH", default=False)
"""是否启用UA透传，默认False，启用后会将请求的User-Agent透传给alist，会导致预缓存失效"""

IGNORE_PATH = env.list("IGNORE_PATH", subcast=str, default=[])
IGNORE_PATH = [path.strip() for path in IGNORE_PATH]

MOUNT_PATH_PREFIX_REMOVE = env.str("MOUNT_PATH_PREFIX_REMOVE", default="").strip()
MOUNT_PATH_PREFIX_ADD = env.str("MOUNT_PATH_PREFIX_ADD", default="").strip()

CACHE_ENABLE = env.bool("CACHE_ENABLE", default=False)
CACHE_NEXT_EPISODE = env.bool("CACHE_NEXT_EPISODE", default=False)
CACHE_PATH = env.str("CACHE_PATH", default="./cache")
FORCE_CLIENT_RECONNECT = env.bool("FORCE_CLIENT_RECONNECT", default=False)

LOG_LEVEL = env.str("LOG_LEVEL", default="info").upper()

# ADVANCED CONFIGURATION 高级配置
INITIAL_CACHE_SIZE_OF_TAIL = env.int("INITIAL_CACHE_SIZE_OF_TAIL", default=3*1024*1024)
"""初始末尾缓存大小，会经过裁切，需要保证请求Range在文件末尾的Cache Size内"""

CHUNK_SIZE_OF_CHUNKSWITER = env.int("CHUNK_SIZE_OF_CHUNKSWITER", default=200*1024)
"""ChunksWriter实例中的缓存块大小，默认为2MB"""

# EXPERIMENTAL CONFIGURATION 实验性配置
MEMORY_CACHE_ONLY = env.bool("MEMORY_CACHE_ONLY", default=False)
"""是否只使用内存缓存，默认False"""

HIGH_COMPAT_MEDIA_CLIENTS = env.list("HIGH_COMPAT_MEDIA_CLIENTS", subcast=str, default=["mpv", "senplayer"])
"""对Range请求支持较好的播放器列表，默认mpv"""
HIGH_COMPAT_MEDIA_CLIENTS = [client.strip().lower() for client in HIGH_COMPAT_MEDIA_CLIENTS]

# CACHE CLEANUP CONFIGURATION 缓存清理配置
CACHE_MAX_SIZE_GB = env.float("CACHE_MAX_SIZE_GB", default=50.0)
"""最大缓存大小(GB)，默认50GB"""

CACHE_CLEANUP_THRESHOLD = env.float("CACHE_CLEANUP_THRESHOLD", default=0.8)
"""缓存清理触发阈值，默认0.8（80%）"""

CACHE_CLEANUP_TARGET = env.float("CACHE_CLEANUP_TARGET", default=0.6)  
"""缓存清理目标，默认0.6（60%）"""

CACHE_MIN_SCORE_THRESHOLD = env.float("CACHE_MIN_SCORE_THRESHOLD", default=200.0)
"""最低分数阈值，低于此分数的缓存会被优先清理"""

CACHE_PROTECTION_DAYS = env.int("CACHE_PROTECTION_DAYS", default=3)
"""缓存保护天数，最近N天访问的缓存不会被清理"""

CACHE_SCORE_RECALC_HOURS = env.int("CACHE_SCORE_RECALC_HOURS", default=24)
"""分数重新计算间隔（小时），默认24小时"""

CACHE_SIZE_CHECK_HOURS = env.int("CACHE_SIZE_CHECK_HOURS", default=1)
"""缓存大小检查间隔（小时），默认1小时"""

CACHE_WEEKLY_CLEANUP = env.bool("CACHE_WEEKLY_CLEANUP", default=False)
"""是否启用每周定时清理，默认关闭（推荐关闭，缓存应该长期保留）"""
