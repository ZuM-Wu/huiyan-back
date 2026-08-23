"""
通知操作 MCP 核心工具（audience=admin）

- notify_farmer  给指定农户发送站内信（写入 hy_inbox_message，农户端收件箱可见）

设计说明: 刻意走站内信而非短信/邮件——notice_sender.send 依赖动作模板配置
（action_key + 模板变量），AI 自由撰写文本的场景不适用；站内信零配置可用、
内容自由，标题含"紧急"关键字时由 inbox_trigger 自动提升为重要优先级。
写操作成功后统一写 active_log（文案带 MCP 前缀便于审计区分）。
"""
import logging

from sqlalchemy import select
from fastmcp.exceptions import ToolError

from core.db.base import async_session_factory
from core.db.farmer import Farmer
from services.mcp.tool_utils import _current_claims, admin_identity

logger = logging.getLogger(__name__)


async def notify_farmer(farmer_id: int, title: str, content: str) -> dict:
    """给指定农户发送一条站内信（农户端收件箱可见）。

    :param farmer_id: 农户ID（由 core_farmer_list 工具获取）
    :param title: 消息标题（必填，含"紧急"关键字自动提升为重要优先级）
    :param content: 消息正文（必填，纯文本）
    :return: 发送结果，含 message_id/farmer_id/username/msg
    """
    # 参数 fail-fast: 空标题/空正文在开库前直接拒绝
    if not title or not title.strip():
        raise ToolError("title 不能为空")
    if not content or not content.strip():
        raise ToolError("content 不能为空")

    claims = _current_claims()

    async with async_session_factory() as db:
        # 查询顺序约定: 1.农户存在性与启用状态（禁用农户不投递）
        farmer = (await db.execute(
            select(Farmer).where(Farmer.id == farmer_id)
        )).scalar_one_or_none()
        if farmer is None:
            raise ToolError(f"农户不存在: {farmer_id}")
        if farmer.status != 1:
            raise ToolError(f"农户已被禁用，无法接收站内信: {farmer.username}")

    # 管理员归因身份反查（sender_id 落库，收件箱可展示发件人）
    admin_id, admin_name = await admin_identity(claims)

    from core.inbox_trigger import inbox_trigger
    message_id = await inbox_trigger.create(
        receiver_id=farmer_id,
        title=title.strip(),
        content=content.strip(),
        receiver_type="farmer",
        sender_id=admin_id,
    )
    if not message_id:
        # inbox_trigger 内部吞异常返回 0，此处转为 AI 客户端可见错误
        raise ToolError("站内信创建失败，请稍后重试")

    from core.log.active_log import active_log
    await active_log(
        f"MCP发送站内信给农户 {farmer.username}: {title.strip()[:50]}"
        f"（发送人: {admin_name}）",
        "notice", rel_id=message_id,
    )
    return {
        "message_id": message_id,
        "farmer_id": farmer_id,
        "username": farmer.username,
        "msg": "站内信已发送",
    }


# 通知操作工具声明列表（tools_core.py 聚合进 CORE_TOOLS）
NOTICE_TOOLS: list[dict] = [
    {
        "name": "notify_farmer",
        "description": "给指定农户发送站内信（农户端收件箱可见）。参数 farmer_id "
                       "为农户ID（由 core_farmer_list 获取），title 消息标题"
                       "（含\"紧急\"关键字自动提升为重要优先级），content 消息正文。"
                       "适用于天气预警提醒、农事建议等 AI 主动干预场景。",
        "handler": notify_farmer,
        "audience": "admin",
        "permission_code": None,
    },
]
