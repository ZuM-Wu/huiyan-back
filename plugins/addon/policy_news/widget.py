"""农业政策首页挂件。"""
from services.widget.widget_engine import BaseWidget

from .service import get_latest


class PolicyNewsWidget(BaseWidget):
    """管理端和农户端共用的政策列表挂件。"""

    name = "policy_news_latest"
    title = "最新农业政策"
    columns = 2
    weight = 60
    widget_type = "list"
    owner = "policy_news"
    audience = "both"

    async def get_data(self) -> dict:
        items = await get_latest(limit=5, per_source=False)
        return {
            "icon": "file-copy",
            "logs": [{
                "id": item["id"],
                "type": item["source"],
                "description": item["title"],
                "user_name": item["source"],
                "create_time": item["published_at"] or "暂无日期",
                "url": item["url"],
            } for item in items],
        }
