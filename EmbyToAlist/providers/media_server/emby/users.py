from ....models import ItemInfo
from .helpers import build_item_info
from .client import EmbyClient

async def get_resume_list(client: EmbyClient, user_id: str) -> list[ItemInfo]:
    """获取用户的播放记录列表

    Args:
        user_id (str): Emby User ID
        api_key (str): Emby API Key

    Returns:
        list[ItemInfo]: 包含Item信息的dataclass列表
    """

    data = await client.get_json(f"/emby/Users/{user_id}/Items/Resume", params={"Limit": 10})

    return [build_item_info(i) for i in data.get('Items', [])]
