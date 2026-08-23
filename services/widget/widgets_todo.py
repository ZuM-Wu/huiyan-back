# -*- coding: utf-8 -*-
"""
待办事项仪表盘挂件

汇总管理员需要关注的待处理事项计数（对标 ZJMF 待办事项卡片样式）：
- 待处理实名: 实名认证记录中状态为待审核（status=0）的数量
- 待手动处理事件: 任务队列中死信（status=Dead）的数量，与死信页保持一致
- 天气预警: 本日入库且当前仍生效的气象灾害预警数量（create_time 落在今日且 end_time 为空或未过期）

条目为开放式清单，后续有新的待办来源时在 get_data 的 items 列表中追加即可（空位先空着）。
"""
import logging
from datetime import datetime

from sqlalchemy import select, func

from services.widget.widget_engine import BaseWidget
from core.db.base import async_session_factory
from core.db.certification import CertificationRecord
from core.db.task_queue import TaskQueue
from core.db.weather import WeatherAlert

logger = logging.getLogger(__name__)


class TodoWidget(BaseWidget):
    """待办事项挂件（与产区积温卡同宽高，网格平铺各待办计数，下方空位留白待扩展）"""
    name = "todo_items"
    title = "待办事项"
    columns = 2
    weight = 40
    widget_type = "todo"

    async def get_data(self) -> dict:
        now = datetime.now()
        async with async_session_factory() as db:
            # 待处理实名：待审核的实名认证记录
            cert_count = (await db.execute(
                select(func.count(CertificationRecord.id))
                .where(CertificationRecord.status == 0)
            )).scalar() or 0

            # 待手动处理事件：与任务监控“死信”页使用同一数据源
            task_count = (await db.execute(
                select(func.count(TaskQueue.id))
                .where(TaskQueue.status == "Dead")
            )).scalar() or 0

            # 天气预警：本日入库且当前仍生效的预警
            # （create_time 落在今日零点后 AND end_time 为空或未过期），
            # 本地 naive 时间口径，与全项目 datetime.now() 一致
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            alert_count = (await db.execute(
                select(func.count(WeatherAlert.id))
                .where(
                    WeatherAlert.create_time >= today_start,
                    (WeatherAlert.end_time.is_(None)) | (WeatherAlert.end_time >= now),
                )
            )).scalar() or 0

        # 每个条目携带跳转地址，前端点击直达对应管理页
        return {
            "items": [
                {
                    "key": "certification",
                    "label": "待处理实名",
                    "icon": "user-checked",
                    "count": cert_count,
                    "url": "/admin/certification",
                },
                {
                    "key": "task_failed",
                    "label": "待手动处理事件",
                    "icon": "task-error",
                    "count": task_count,
                    "url": "/admin/task_monitor?tab=dead",
                },
                {
                    "key": "weather_alert",
                    "label": "天气预警",
                    "icon": "error-triangle",
                    "count": alert_count,
                    "url": "/admin/weather?tab=alerts",
                },
            ],
        }


def register_todo_widgets(engine):
    """注册待办事项挂件到引擎"""
    engine.register(TodoWidget())
    logger.info("[WidgetEngine] 已注册待办事项挂件: todo_items")
