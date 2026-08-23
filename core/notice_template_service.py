"""
通知模板查询服务层

封装对 core.db.notice 模块中 SmsTemplate / EmailTemplate 的数据库查询逻辑，
供 api 层调用。所有函数返回纯 dict/list，不返回 ORM 实例。
"""
import json
import logging

from sqlalchemy import select, func, or_, and_

from core.db.base import async_session_factory
from core.db.notice import SmsTemplate, EmailTemplate

logger = logging.getLogger(__name__)


# ========== 内部辅助函数 ==========

def _fmt_dt(dt) -> str:
    """格式化 datetime 为字符串，None 返回空串"""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _sms_template_to_dict(t: SmsTemplate) -> dict:
    """SmsTemplate ORM 实例转 dict"""
    return {
        "id": t.id,
        "interface": t.interface,
        "is_local": not bool(t.interface),
        "type": t.type,
        "template_id": t.template_id,
        "title": t.title,
        "content": t.content,
        "signature": t.signature,
        "status": t.status,
        "third_status": t.third_status,
        "action_key": t.action_key,
        "remark": t.remark,
        "create_time": _fmt_dt(t.create_time),
        "update_time": _fmt_dt(t.update_time),
    }


def _email_template_to_dict(t: EmailTemplate) -> dict:
    """EmailTemplate ORM 实例转 dict"""
    return {
        "id": t.id,
        "name": t.name,
        "subject": t.subject,
        "content": t.content,
        "attachment": t.attachment,
        "action_key": t.action_key,
        "create_time": _fmt_dt(t.create_time),
        "update_time": _fmt_dt(t.update_time),
    }


# ========== 短信模板 ==========

async def list_sms_templates(
    page: int = 1, limit: int = 20, keywords: str = ""
) -> dict:
    """短信模板分页列表

    :param page:    页码
    :param limit:   每页条数
    :param keywords: 搜索关键词（匹配标题/内容）
    :return: {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        q = select(SmsTemplate)
        if keywords:
            q = q.where(or_(
                SmsTemplate.title.contains(keywords),
                SmsTemplate.content.contains(keywords),
            ))

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(SmsTemplate.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_sms_template_to_dict(r) for r in rows],
        }


async def get_sms_template(template_id: int) -> dict | None:
    """获取短信模板详情

    :param template_id: 模板 ID
    :return: 模板 dict，不存在返回 None
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(SmsTemplate).where(SmsTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            return None
        return _sms_template_to_dict(template)


async def create_sms_template(data: dict) -> int:
    """创建短信模板

    :param data: 模板字段字典
    :return: 新建模板 ID
    """
    async with async_session_factory() as db:
        template = SmsTemplate(**data)
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return template.id


async def update_sms_template(template_id: int, data: dict) -> bool:
    """更新短信模板

    :param template_id: 模板 ID
    :param data:        待更新字段字典（仅更新非 None 值）
    :return: 是否更新成功
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(SmsTemplate).where(SmsTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            return False

        for field, value in data.items():
            if value is not None:
                setattr(template, field, value)

        await db.commit()
        return True


async def delete_sms_template(template_id: int) -> bool:
    """删除短信模板

    :param template_id: 模板 ID
    :return: 是否删除成功
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(SmsTemplate).where(SmsTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            return False

        await db.delete(template)
        await db.commit()
        return True


# ========== 邮件模板 ==========

async def list_email_templates(
    page: int = 1, limit: int = 20, keywords: str = ""
) -> dict:
    """邮件模板分页列表

    :param page:    页码
    :param limit:   每页条数
    :param keywords: 搜索关键词（匹配名称/主题）
    :return: {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        q = select(EmailTemplate)
        if keywords:
            q = q.where(or_(
                EmailTemplate.name.contains(keywords),
                EmailTemplate.subject.contains(keywords),
            ))

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(EmailTemplate.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_email_template_to_dict(r) for r in rows],
        }


async def get_email_template(template_id: int) -> dict | None:
    """获取邮件模板详情

    :param template_id: 模板 ID
    :return: 模板 dict，不存在返回 None
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            return None
        return _email_template_to_dict(template)


async def get_email_template_by_action(action_key: str) -> dict | None:
    """按动作键查询邮件模板（供 admin_notifier 插件使用）

    :param action_key: 动作标识
    :return: 模板 dict，不存在返回 None
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.action_key == action_key)
        )
        template = result.scalar_one_or_none()
        if not template:
            return None
        return _email_template_to_dict(template)


async def create_email_template(data: dict) -> int:
    """创建邮件模板

    :param data: 模板字段字典
    :return: 新建模板 ID
    """
    async with async_session_factory() as db:
        template = EmailTemplate(**data)
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return template.id


async def update_email_template(template_id: int, data: dict) -> bool:
    """更新邮件模板

    :param template_id: 模板 ID
    :param data:        待更新字段字典（仅更新非 None 值）
    :return: 是否更新成功
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            return False

        for field, value in data.items():
            if value is not None:
                setattr(template, field, value)

        await db.commit()
        return True


async def delete_email_template(template_id: int) -> bool:
    """删除邮件模板

    :param template_id: 模板 ID
    :return: 是否删除成功
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            return False

        await db.delete(template)
        await db.commit()
        return True


# ========== 短信模板扩展 ==========

async def search_sms_templates(
    page: int = 1, limit: int = 20,
    interface: str = "", type: int | None = None, status: int | None = None,
    action_key: str = "", keywords: str = "",
) -> dict:
    """短信模板分页列表（支持多条件筛选）"""
    async with async_session_factory() as db:
        q = select(SmsTemplate)
        conditions = []
        if interface == "__local__":
            conditions.append(SmsTemplate.interface == "")
        elif interface:
            conditions.append(SmsTemplate.interface == interface)
        if type is not None:
            conditions.append(SmsTemplate.type == type)
        if status is not None:
            conditions.append(SmsTemplate.status == status)
        if action_key:
            conditions.append(SmsTemplate.action_key == action_key)
        if keywords:
            conditions.append(or_(
                SmsTemplate.title.contains(keywords),
                SmsTemplate.content.contains(keywords),
            ))
        if conditions:
            q = q.where(and_(*conditions))

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(SmsTemplate.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_sms_template_to_dict(r) for r in rows],
        }


async def check_sms_template_exists(interface: str, template_id: str) -> bool:
    """检查指定 interface + template_id 的短信模板是否已存在"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(SmsTemplate).where(
                SmsTemplate.interface == interface,
                SmsTemplate.template_id == template_id,
            )
        )
        return result.scalar_one_or_none() is not None


async def list_sms_templates_by_ids(template_ids: list[int]) -> list[dict]:
    """按 ID 列表获取短信模板"""
    if not template_ids:
        return []
    async with async_session_factory() as db:
        result = await db.execute(
            select(SmsTemplate).where(SmsTemplate.id.in_(template_ids))
        )
        return [_sms_template_to_dict(r) for r in result.scalars().all()]


async def list_sms_templates_by_interface_status(
    interface: str, status: int,
) -> list[dict]:
    """按 interface + status 获取短信模板列表"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(SmsTemplate).where(
                SmsTemplate.interface == interface,
                SmsTemplate.status == status,
            )
        )
        return [_sms_template_to_dict(r) for r in result.scalars().all()]


async def sync_sms_templates_from_remote(
    templates: list, plugin_name: str,
) -> int:
    """将远程平台模板写入 hy_sms_template（按接口和模板 ID 去重）"""
    async with async_session_factory() as db:
        synced_count = 0
        for tpl in templates:
            tpl_id = str(tpl.get("template_id") or tpl.get("id", ""))
            if not tpl_id:
                continue
            existing = (await db.execute(
                select(SmsTemplate).where(
                    SmsTemplate.interface == plugin_name,
                    SmsTemplate.template_id == tpl_id,
                )
            )).scalar_one_or_none()
            if existing:
                continue
            raw_type = tpl.get("type", tpl.get("sms_type", 0))
            try:
                tpl_type = 1 if int(raw_type) == 1 else 0
            except (TypeError, ValueError):
                tpl_type = 0
            raw_status = tpl.get("status", tpl.get("template_status", 2))
            try:
                status = max(0, min(3, int(raw_status)))
            except (TypeError, ValueError):
                status = 2
            third_status = tpl.get("third_status")
            if isinstance(third_status, (dict, list)):
                third_status = json.dumps(third_status, ensure_ascii=False)
            db.add(SmsTemplate(
                title=tpl.get("title") or tpl.get("name") or "未命名模板",
                content=tpl.get("content") or tpl.get("template_content") or "",
                template_id=tpl_id,
                interface=plugin_name,
                type=tpl_type,
                signature=tpl.get("signature") or tpl.get("sign") or "",
                action_key=tpl.get("action_key") or "",
                remark=tpl.get("remark") or "",
                third_status=third_status,
                status=status,
            ))
            synced_count += 1
        await db.commit()
        return synced_count


# ========== 邮件模板扩展 ==========

async def search_email_templates(
    page: int = 1, limit: int = 20,
    keywords: str = "", action_key: str = "",
) -> dict:
    """邮件模板分页列表（支持关键词与 action_key 筛选）"""
    async with async_session_factory() as db:
        q = select(EmailTemplate)
        conditions = []
        if action_key:
            conditions.append(EmailTemplate.action_key == action_key)
        if keywords:
            conditions.append(or_(
                EmailTemplate.name.contains(keywords),
                EmailTemplate.subject.contains(keywords),
            ))
        if conditions:
            q = q.where(and_(*conditions))

        total = (await db.execute(
            select(func.count()).select_from(q.subquery())
        )).scalar() or 0

        rows = (await db.execute(
            q.order_by(EmailTemplate.id.desc())
            .offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        return {
            "total": total, "page": page, "limit": limit,
            "list": [_email_template_to_dict(r) for r in rows],
        }


async def get_email_template_by_name(name: str) -> dict | None:
    """按名称查询邮件模板（用于唯一性校验）"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(EmailTemplate).where(EmailTemplate.name == name)
        )
        template = result.scalar_one_or_none()
        if not template:
            return None
        return _email_template_to_dict(template)
