# -*- coding: utf-8 -*-
"""
农业知识库插件 — MCP 工具 handler（模块级 async 函数）

约定（与 core 工具一致）：
- handler 为模块级函数（非 Plugin 方法），工具 schema 由函数签名生成，
  不能携带 self；内部用 async_session_factory() 自建 DB 会话。
- 工具实际注册名由 registry 自动加插件名前缀（knowledge_search 等）。
"""
import logging

from sqlalchemy import select

from core.db.base import async_session_factory
from plugins.addon.knowledge.models import KnowledgeCategory, KnowledgeEntry
from plugins.addon.knowledge.services.category_service import CategoryService
from plugins.addon.knowledge.services.entry_service import EntryService
from services.mcp.errors import McpToolError
from services.mcp.tool_utils import current_claims, positive_id

logger = logging.getLogger(__name__)

# 搜索返回条数上限（防止 AI 一次拉取过多导致上下文膨胀）
SEARCH_MAX_LIMIT = 50
BATCH_MAX_TITLES = 50
TEXT_LIMITS = {"title": 200, "crop": 200, "summary": 500, "cause": 10000, "solution": 10000}


def _admin_id() -> int:
    """返回当前 MCP 管理员 ID，禁止使用虚构的系统管理员。"""
    claims = current_claims()
    if str(claims.get("user_type", "")).lower() != "admin":
        raise McpToolError("permission_denied", "知识库写入仅限管理员")
    return positive_id(claims.get("user_id"), "user_id")


def _text(value: str, field: str, *, required: bool = False) -> str:
    value = str(value or "").strip()
    if required and not value:
        raise McpToolError("invalid_arguments", f"{field} 不能为空")
    if len(value) > TEXT_LIMITS[field]:
        raise McpToolError("invalid_arguments", f"{field} 不能超过{TEXT_LIMITS[field]}字符")
    return value


def _active_category(category, category_id: int) -> None:
    if category is None:
        raise McpToolError("resource_not_found", f"分类不存在: {category_id}")
    status = getattr(category, "status", None)
    if isinstance(status, int) and status != 1:
        raise McpToolError("operation_failed", "分类已停用")


async def knowledge_search(
    keyword: str = "", category_id: int = 0, crop: str = "", limit: int = 20,
) -> list[dict]:
    """搜索农业知识条目。

    :param keyword: 标题/摘要关键词
    :param category_id: 分类ID（子类或大类），0=不限
    :param crop: 适用作物关键词（如 小麦、水稻）
    :param limit: 返回条数（1~50）
    :return: 条目摘要列表（id/title/分类名/crop/summary）
    """
    try:
        limit = max(1, min(int(limit or 20), SEARCH_MAX_LIMIT))
    except (TypeError, ValueError):
        raise McpToolError("invalid_arguments", "limit 必须是整数") from None
    if category_id:
        category_id = positive_id(category_id, "category_id")
    crop = _text(crop, "crop")
    async with async_session_factory() as db:
        result = await EntryService.list_entries(
            db, keyword=keyword, category_id=category_id, crop=crop,
            page=1, limit=limit,
        )
        names = await CategoryService.name_map(db)
    return [
        {
            "id": row["id"], "title": row["title"],
            "category_id": row["category_id"],
            "category_name": names.get(row["category_id"], ""),
            "crop": row["crop"], "summary": row["summary"],
        }
        for row in result["list"]
    ]


async def knowledge_detail(entry_id: int) -> dict:
    """查询农业知识条目详情（含典型图片/发生原因/解决方案）。

    :param entry_id: 知识条目ID
    :return: 完整字段字典；条目不存在返回 resource_not_found 业务错误
    """
    entry_id = positive_id(entry_id, "entry_id")
    async with async_session_factory() as db:
        row = await EntryService.get_entry(db, entry_id)
        if not row:
            raise McpToolError("resource_not_found", f"知识条目不存在: {entry_id}")
        names = await CategoryService.name_map(db)
        detail = EntryService.to_detail(row)
    detail["category_name"] = names.get(detail["category_id"], "")
    return detail


async def knowledge_categories() -> list[dict]:
    """列出农业知识库的二级分类树（仅启用分类）。

    :return: 大类列表，每个大类含 children 子类
    """
    async with async_session_factory() as db:
        return await CategoryService.list_tree(db, only_enabled=True)


async def knowledge_create(
    title: str, category_id: int, crop: str = "", summary: str = "",
    cause: str = "", solution: str = "",
) -> dict:
    """新建农业知识条目（不含典型图片，需后续在后台编辑上传补充）。

    :param title: 知识标题（必填，如 小麦白粉病）
    :param category_id: 所属分类ID（必填，指向子类或大类）
    :param crop: 适用作物，逗号分隔（如 小麦,大麦）
    :param summary: 摘要
    :param cause: 发生原因
    :param solution: 解决方案
    :return: 创建结果，含新条目 id
    """
    admin_id = _admin_id()
    category_id = positive_id(category_id, "category_id")
    title = _text(title, "title", required=True)
    crop = _text(crop, "crop")
    summary = _text(summary, "summary")
    cause = _text(cause, "cause")
    solution = _text(solution, "solution")
    async with async_session_factory() as db:
        category = (await db.execute(
            select(KnowledgeCategory).where(KnowledgeCategory.id == category_id)
        )).scalar_one_or_none()
        _active_category(category, category_id)
        entry = KnowledgeEntry(
            title=title, category_id=category_id, crop=crop,
            summary=summary, cause=cause, solution=solution,
            images="", admin_id=admin_id,
        )
        try:
            db.add(entry)
            await db.commit()
        except Exception:
            await db.rollback()
            raise McpToolError("operation_failed", "知识条目创建失败") from None
        await db.refresh(entry)
        from core.log.active_log import active_log
        await active_log(f"MCP新增知识条目: {entry.title}", "knowledge",
                         rel_id=entry.id, db=db)
        return {
            "id": entry.id,
            "msg": f"知识条目已创建: {entry.title}（典型图片请在后台编辑中上传补充）",
        }


async def knowledge_update(
    entry_id: int, title: str = "", category_id: int = 0, crop: str = "",
    summary: str = "", cause: str = "", solution: str = "",
) -> dict:
    """更新农业知识条目（增量语义：仅写入非空参数，未传字段保持原值）。

    典型图片不可经 MCP 修改，需在后台条目编辑弹窗上传。

    :param entry_id: 知识条目ID（必填，由 knowledge_search 获取）
    :param title: 新标题（空串表示不修改）
    :param category_id: 新分类ID（0 表示不修改）
    :param crop: 新适用作物，逗号分隔（空串表示不修改）
    :param summary: 新摘要（空串表示不修改）
    :param cause: 新发生原因（空串表示不修改）
    :param solution: 新解决方案（空串表示不修改）
    :return: 更新结果，含 id/updated 实际写入的字段名列表
    """
    admin_id = _admin_id()
    entry_id = positive_id(entry_id, "entry_id")
    # 增量语义: 只收集非空参数（服务层 update_entry 为整体覆盖且会连
    # images 一起覆写，故此处直接 ORM 局部更新，不复用服务层）
    changes: dict = {}
    if title and title.strip():
        changes["title"] = _text(title, "title")
    for field, value in (("crop", crop), ("summary", summary),
                         ("cause", cause), ("solution", solution)):
        if value:
            changes[field] = _text(value, field)
    if not changes and not category_id:
        raise McpToolError("invalid_arguments", "未提供任何待更新字段")

    async with async_session_factory() as db:
        entry = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        )).scalar_one_or_none()
        if entry is None:
            raise McpToolError("resource_not_found", f"知识条目不存在: {entry_id}")
        if category_id:
            category = (await db.execute(
                select(KnowledgeCategory).where(KnowledgeCategory.id == category_id)
            )).scalar_one_or_none()
            _active_category(category, category_id)
            changes["category_id"] = category_id
        for field, value in changes.items():
            setattr(entry, field, value)
        entry.admin_id = admin_id
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise McpToolError("operation_failed", "知识条目更新失败") from None
        from core.log.active_log import active_log
        await active_log(f"MCP更新知识条目: {entry.title}", "knowledge",
                         rel_id=entry_id, db=db)
        return {
            "id": entry_id,
            "updated": sorted(changes.keys()),
            "msg": f"知识条目已更新: {entry.title}",
        }


async def knowledge_batch_create(
    category_id: int, titles: list[str], crop: str = "",
) -> dict:
    """批量新建农业知识条目（共享分类/作物，仅录标题）。

    批量不含典型图片，需后续在后台补图与补充正文。

    :param category_id: 所属分类ID（必填，批量共享）
    :param titles: 标题列表（必填，逐条建为独立条目）
    :param crop: 适用作物，逗号分隔（批量共享，选填）
    :return: 创建结果，含 created 条数与新条目 id 列表
    """
    admin_id = _admin_id()
    category_id = positive_id(category_id, "category_id")
    cleaned = [t.strip() for t in (titles or []) if t and t.strip()]
    if not cleaned:
        raise McpToolError("invalid_arguments", "titles 不能为空")
    if len(cleaned) > BATCH_MAX_TITLES:
        raise McpToolError("invalid_arguments", f"titles 最多支持{BATCH_MAX_TITLES}条")
    cleaned = [_text(title, "title", required=True) for title in cleaned]
    crop = _text(crop, "crop")
    async with async_session_factory() as db:
        category = (await db.execute(
            select(KnowledgeCategory).where(KnowledgeCategory.id == category_id)
        )).scalar_one_or_none()
        _active_category(category, category_id)
        ids = await EntryService.batch_create(
            db, category_id, cleaned, crop, admin_id
        )
        from core.log.active_log import active_log
        await active_log(f"MCP批量新增知识条目: {len(ids)}条", "knowledge", db=db)
    return {
        "created": len(ids),
        "ids": ids,
        "msg": f"已批量创建 {len(ids)} 条知识条目（典型图片请在后台编辑中上传补充）",
    }

