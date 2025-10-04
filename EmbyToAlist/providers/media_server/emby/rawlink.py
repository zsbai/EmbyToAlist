import asyncio
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from fastapi import HTTPException
from httpx import RequestError, TransportError
from loguru import logger

from ....config import RAW_LINK_EMBY_SERVER, RAW_LINK_EMBY_KEY
from ....utils.common import ClientManager
from .client import EmbyClient
from .exceptions import EmbyHTTPError


def _ensure_absolute_url(base: str, url: str) -> str:
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = base.rstrip('/')
    if not url.startswith('/'):
        url = '/' + url
    return f"{base}{url}"


def _ensure_api_key(url: str, api_key: str) -> str:
    if not api_key:
        return url
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        if any(k.lower() in ("api_key", "apikey", "x-emby-token") for k in q.keys()):
            return url
        q["api_key"] = [api_key]
        new_query = urlencode({k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in q.items()}, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        # 保守兜底
        sep = '&' if ('?' in url) else '?'
        return f"{url}{sep}api_key={api_key}"


def _choose_media_source(media_sources: list[dict], *, file_ext: str | None = None) -> dict | None:
    if not media_sources:
        return None
    def matches_ext(ms: dict) -> bool:
        if not file_ext:
            return False
        cont = (ms.get('Container') or '').lower()
        return cont == file_ext.lower()
    # 优先 SupportsDirectPlay 且扩展名一致
    for ms in media_sources:
        if ms.get('SupportsDirectPlay') and matches_ext(ms):
            return ms
    # 次选 SupportsDirectPlay
    for ms in media_sources:
        if ms.get('SupportsDirectPlay'):
            return ms
    # 次选扩展名一致
    for ms in media_sources:
        if matches_ext(ms):
            return ms
    # 兜底第一个
    return media_sources[0]


async def get_emby_raw_url(file_path: str, user_id: str | None = None, max_retries: int = 3, retry_delay: float = 0.2) -> str:
    """
    基于第二台 Emby，通过 Path 定位 Item 并返回 DirectStream 直链。

    Args:
        file_path: 第二台 Emby 库中的真实文件系统路径
        user_id: 可选的 Emby 用户 ID（当前不强制）
        max_retries: 网络/服务错误的最大重试次数
        retry_delay: 重试间隔（秒）
    Returns:
        直链 URL（绝对地址，包含 api_key）
    """
    if not RAW_LINK_EMBY_SERVER or not RAW_LINK_EMBY_KEY:
        logger.error("RAW_LINK_EMBY_SERVER/RAW_LINK_EMBY_KEY 未配置")
        raise HTTPException(status_code=500, detail="Emby RawLink 配置缺失")

    client = EmbyClient(base_url=RAW_LINK_EMBY_SERVER, api_key=RAW_LINK_EMBY_KEY, client=ClientManager.get_client())

    # 推断文件扩展名（用于 MediaSource 选择）
    file_ext = None
    try:
        dot_idx = file_path.rfind('.')
        if dot_idx > 0:
            file_ext = file_path[dot_idx + 1 :].lower()
    except Exception:
        file_ext = None

    for attempt in range(max_retries + 1):
        try:
            # 1) 通过 Items 查询（Path + Recursive）
            logger.debug(f"[EmbyRaw] Query Items by Path (attempt {attempt+1}/{max_retries+1})")
            data = await client.get_json(
                "/emby/Items",
                params={
                    "Path": file_path,
                    "Recursive": True,
                    "Fields": "Path,Container,MediaSources",
                },
            )
            items = [i for i in data.get('Items', []) if (i.get('Path') == file_path)]

            # 2) 兜底 ByPath
            if not items:
                logger.debug("[EmbyRaw] Items empty, fallback to ByPath")
                try:
                    bypath = await client.get_json("/emby/Items/ByPath", params={"Path": file_path})
                    if bypath:
                        items = [bypath]
                except EmbyHTTPError as e:
                    if e.status_code == 404:
                        items = []
                    else:
                        raise

            if not items:
                logger.warning(f"[EmbyRaw] Item not found by path: {file_path}")
                raise HTTPException(status_code=404, detail="Emby Item Not Found")
            if len(items) > 1:
                logger.error(f"[EmbyRaw] Multiple items matched for path: {file_path}")
                raise HTTPException(status_code=500, detail="Multiple Emby Items matched")

            item = items[0]
            item_id = item.get('Id')
            if not item_id:
                raise HTTPException(status_code=500, detail="Invalid Emby Item: missing Id")

            # 3) 获取 PlaybackInfo（POST，更通用）
            logger.debug(f"[EmbyRaw] Fetch PlaybackInfo for item {item_id}")
            pb = await client.post_json(
                f"/emby/Items/{item_id}/PlaybackInfo",
                params={"UserId": user_id} if user_id else None,
                json_body={},
            )
            media_sources = pb.get('MediaSources') or []
            if not media_sources:
                raise HTTPException(status_code=500, detail="No MediaSources in PlaybackInfo")

            ms = _choose_media_source(media_sources, file_ext=file_ext)
            if not ms:
                raise HTTPException(status_code=500, detail="No suitable MediaSource found")

            direct = ms.get('DirectStreamUrl') or ''
            if not direct:
                # 回退构造直链（不强加 static=true，必要时再开启）
                ms_id = ms.get('Id')
                if not ms_id:
                    raise HTTPException(status_code=500, detail="MediaSource missing Id for fallback")
                path = f"/emby/Videos/{item_id}/stream?MediaSourceId={ms_id}"
                direct = _ensure_absolute_url(RAW_LINK_EMBY_SERVER, path)
                direct = _ensure_api_key(direct, RAW_LINK_EMBY_KEY)
            else:
                direct = _ensure_absolute_url(RAW_LINK_EMBY_SERVER, direct)
                direct = _ensure_api_key(direct, RAW_LINK_EMBY_KEY)

            logger.debug(f"[EmbyRaw] Direct url: {direct}")
            return direct

        except EmbyHTTPError as e:
            # 仅对 5xx 重试
            if e.status_code and int(e.status_code) >= 500 and attempt < max_retries:
                logger.warning(f"[EmbyRaw] EmbyHTTPError {e.status_code}, retrying...")
                await asyncio.sleep(retry_delay)
                continue
            logger.error(f"[EmbyRaw] EmbyHTTPError: {e}")
            raise HTTPException(status_code=500, detail="Emby Server Error")
        except (RequestError, TransportError) as e:
            if attempt < max_retries:
                logger.warning(f"[EmbyRaw] Request error: {e}, retrying...")
                await asyncio.sleep(retry_delay)
                continue
            logger.error(f"[EmbyRaw] Request failed: {e}")
            raise HTTPException(status_code=500, detail="Emby Request Failed")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[EmbyRaw] Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Emby RawLink Error")
