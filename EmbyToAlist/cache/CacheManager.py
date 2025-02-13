from .CacheSystem import CacheSystem

class CacheManager():
    _cache_system: CacheSystem = None
    
    @classmethod
    def init_cache(cls, root_dir: str):
        cls._cache_system = CacheSystem(root_dir)
        
    @classmethod
    def get_cache_system(cls) -> CacheSystem:
        return cls._cache_system