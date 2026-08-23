"""实名认证服务层

封装对 hy_certification_record 表的查询与操作，供 api 层调用。
所有函数返回纯 dict/list，不返回 ORM 实例。

设计要点:
- 认证记录列表支持关键词搜索（用户名、真实姓名、身份证号、认证ID）
- 审核操作更新 status、review_time、reviewer_id 等字段
- create_certification 仅创建记录（status=0 待审核），不触发插件逻辑
  （插件 certify_initialize/certify_query 逻辑保留在 API 层）
- 列表查询关联 Farmer 表获取农户用户名
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func, or_

from core.db.base import async_session_factory
from core.db.certification import CertificationRecord
from core.db.farmer import Farmer

logger = logging.getLogger(__name__)


def _record_to_dict(r: CertificationRecord, farmer_name: str = "") -> dict:
    """将认证记录 ORM 行转换为输出字典

    Args:
        r:           ORM 记录行
        farmer_name: 农户用户名（外联查询得到，默认空字符串）

    Returns:
        包含全部展示字段的字典
    """
    return {
        "id": r.id,
        "farmer_id": r.farmer_id,
        "farmer_name": farmer_name,
        "real_name": r.real_name,
        "id_card": r.id_card,
        "cert_type": r.cert_type,
        "cert_no": r.cert_no,
        "status": r.status,
        "phone": r.phone,
        "front_image": r.front_image,
        "back_image": r.back_image,
        "channel": r.channel,
        "submit_time": str(r.submit_time) if r.submit_time else None,
        "review_time": str(r.review_time) if r.review_time else None,
        "reviewer_id": r.reviewer_id,
        "reviewer_name": r.reviewer_name,
        "review_remark": r.review_remark,
        "create_time": str(r.create_time) if r.create_time else None,
    }


async def list_certifications(
    page: int = 1,
    limit: int = 10,
    status: Optional[int] = None,
    keywords: str = "",
) -> dict:
    """认证记录分页列表（支持搜索与状态筛选）

    Args:
        page:     页码（从 1 开始）
        limit:    每页条数
        status:   状态筛选（0=待审核, 1=已认证, 2=未通过），None 则不筛选
        keywords: 搜索关键词（匹配用户名、真实姓名、身份证号、认证ID）

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    async with async_session_factory() as db:
        # 关联 Farmer 表获取用户名
        q = select(CertificationRecord, Farmer.username).outerjoin(
            Farmer, Farmer.id == CertificationRecord.farmer_id
        )
        # 关键词搜索
        if keywords:
            q = q.where(
                or_(
                    Farmer.username.contains(keywords),
                    CertificationRecord.real_name.contains(keywords),
                    CertificationRecord.cert_no.contains(keywords),
                    CertificationRecord.id_card.contains(keywords),
                )
            )
        # 状态筛选
        if status is not None:
            q = q.where(CertificationRecord.status == status)

        # 统计总数
        total = (
            await db.execute(select(func.count()).select_from(q.subquery()))
        ).scalar() or 0

        # 分页查询（按 ID 倒序）
        rows = (
            await db.execute(
                q.order_by(CertificationRecord.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_record_to_dict(r, fname or "") for r, fname in rows],
    }


async def get_certification(cert_id: int) -> Optional[dict]:
    """获取认证记录详情

    Args:
        cert_id: 认证记录 ID

    Returns:
        认证记录字典（含农户用户名），不存在则返回 None
    """
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(CertificationRecord, Farmer.username)
                .outerjoin(Farmer, Farmer.id == CertificationRecord.farmer_id)
                .where(CertificationRecord.id == cert_id)
            )
        ).first()

    if not row:
        return None
    r, fname = row
    return _record_to_dict(r, fname or "")


async def create_certification(
    farmer_id: int, real_name: str, id_card: str, **extra
) -> int:
    """提交认证记录（创建 status=0 的待审核记录）

    仅创建核心数据记录，不触发第三方插件认证流程。
    插件逻辑（certify_initialize / certify_query）由 API 层负责调用。

    Args:
        farmer_id:  农户 ID
        real_name:  真实姓名
        id_card:    身份证号
        **extra:    额外字段，支持:
                    - phone:        手机号
                    - front_image:  身份证正面照 URL
                    - back_image:   身份证背面照 URL
                    - channel:      认证渠道（默认 manual）
                    - cert_type:    认证类型（默认 personal）
                    - cert_no:      认证ID（第三方返回）
                    - certify_url:  第三方认证链接

    Returns:
        新创建的记录 ID
    """
    async with async_session_factory() as db:
        record = CertificationRecord(
            farmer_id=farmer_id,
            real_name=real_name,
            id_card=id_card,
            cert_type=extra.get("cert_type", "personal"),
            phone=extra.get("phone", ""),
            front_image=extra.get("front_image", ""),
            back_image=extra.get("back_image", ""),
            cert_no=extra.get("cert_no", ""),
            certify_url=extra.get("certify_url", ""),
            status=0,
            channel=extra.get("channel", "manual"),
            submit_time=datetime.now(),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

    return record.id


async def review_certification(
    cert_id: int, status: int, review_remark: str, admin_id: int,
    db=None, commit: bool = True,
) -> bool:
    """审核认证记录（通过或拒绝）

    更新记录的 status、review_time、reviewer_id、review_remark 字段。
    注意：admin_name 参数当前未包含在签名中，reviewer_name 需由 API 层
    通过其他方式设置（或在调用前补充）。

    Args:
        cert_id:       认证记录 ID
        status:        审核状态（1=通过, 2=拒绝）
        review_remark: 审核备注
        admin_id:      审核管理员 ID

    Returns:
        True=审核成功, False=记录不存在
    """
    async def _review(session) -> bool:
        record = (
            await session.execute(
                select(CertificationRecord).where(
                    CertificationRecord.id == cert_id
                )
            )
        ).scalar_one_or_none()
        if not record:
            return False

        record.status = status
        record.review_time = datetime.now()
        record.reviewer_id = admin_id
        record.review_remark = review_remark
        if commit:
            await session.commit()
        else:
            await session.flush()
        return True
    if db is not None:
        return await _review(db)
    async with async_session_factory() as session:
        return await _review(session)


async def get_latest_certification(farmer_id: int) -> Optional[dict]:
    """获取农户的最新认证记录

    按记录 ID 倒序取第一条，用于农户端展示当前认证状态。

    Args:
        farmer_id: 农户 ID

    Returns:
        最新认证记录字典，无记录则返回 None
    """
    async with async_session_factory() as db:
        record = (
            await db.execute(
                select(CertificationRecord)
                .where(CertificationRecord.farmer_id == farmer_id)
                .order_by(CertificationRecord.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if not record:
        return None
    return _record_to_dict(record)


async def list_farmer_certifications(
    farmer_id: int, page: int = 1, limit: int = 10
) -> dict:
    """获取农户的认证记录列表（分页）

    Args:
        farmer_id: 农户 ID
        page:      页码（从 1 开始）
        limit:     每页条数

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    async with async_session_factory() as db:
        base_q = select(CertificationRecord).where(
            CertificationRecord.farmer_id == farmer_id
        )
        # 统计总数
        total = (
            await db.execute(select(func.count()).select_from(base_q.subquery()))
        ).scalar() or 0
        # 分页查询（按 ID 倒序）
        rows = (
            await db.execute(
                base_q.order_by(CertificationRecord.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_record_to_dict(r) for r in rows],
    }
