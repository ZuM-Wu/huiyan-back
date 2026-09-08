"""农户查询与 CRUD 服务层

封装 core.db.farmer/system_log/certification/production_area 的查询，
供 api 层和插件层调用，返回纯 dict/list，避免外部模块直接 import core.db.*。
"""
import logging
from datetime import datetime
from core.time_utils import china_now
from typing import Any

from sqlalchemy import select, update, func, desc, and_, or_
from sqlalchemy.sql.elements import ColumnElement

from core.db.base import async_session_factory
from core.db.farmer import Farmer, FarmerLogin, norm_or_none
from core.db.production_area import AreaFarmer, ProductionArea
from core.db.system_log import SystemLog
from core.db.certification import CertificationRecord

logger = logging.getLogger(__name__)


# ==================== 内部辅助 ====================

async def _query_bound_area_names(db, farmer_ids: list[int]) -> dict[int, list[str]]:
    """批量查询农户绑定的产区名称映射（多对多，绑定唯一来源 hy_area_farmer）

    返回 {farmer_id: [产区名, ...]}，未绑定的农户不在映射中。
    """
    if not farmer_ids:
        return {}
    rows = (await db.execute(
        select(AreaFarmer.farmer_id, ProductionArea.name)
        .join(ProductionArea, AreaFarmer.area_id == ProductionArea.id)
        .where(AreaFarmer.farmer_id.in_(farmer_ids))
        .order_by(AreaFarmer.area_id)
    )).all()
    area_map: dict[int, list[str]] = {}
    for fid, name in rows:
        area_map.setdefault(fid, []).append(name)
    return area_map


def _build_filter_conditions(filters: dict[str, Any]) -> list[ColumnElement[bool]]:
    """根据筛选条件构建 SQLAlchemy 条件列表（供 query/count 共用）

    支持的筛选键：farmer_ids / has_phone / has_email / has_plot / area_name /
    register_start / register_end / area / company / country / keyword
    """
    conditions: list[ColumnElement[bool]] = []
    # 指定农户 ID 列表（mode=specified 时由 target_resolver 传入）
    if filters.get("farmer_ids"):
        conditions.append(Farmer.id.in_(filters["farmer_ids"]))
    # 绑定手机
    if filters.get("has_phone"):
        conditions.append(Farmer.phone.isnot(None))
        conditions.append(Farmer.phone != "")
    # 绑定邮箱
    if filters.get("has_email"):
        conditions.append(Farmer.email.isnot(None))
        conditions.append(Farmer.email != "")
    # 绑定产区/地块（通过 hy_area_farmer 关联表，可选按产区名筛选）
    if filters.get("has_plot"):
        subq = select(AreaFarmer.farmer_id)
        if filters.get("area_name"):
            subq = subq.join(ProductionArea, AreaFarmer.area_id == ProductionArea.id)
            subq = subq.where(ProductionArea.name.like(f"%{filters['area_name']}%"))
        conditions.append(Farmer.id.in_(subq))
    # 注册时间范围
    if filters.get("register_start"):
        try:
            conditions.append(Farmer.create_time >= datetime.fromisoformat(filters["register_start"]))
        except (ValueError, TypeError):
            pass
    if filters.get("register_end"):
        try:
            conditions.append(Farmer.create_time <= datetime.fromisoformat(filters["register_end"]))
        except (ValueError, TypeError):
            pass
    # 地区筛选（模糊匹配地址）
    if filters.get("area"):
        conditions.append(Farmer.address.like(f"%{filters['area']}%"))
    # 公司/农场筛选
    if filters.get("company"):
        conditions.append(Farmer.company.like(f"%{filters['company']}%"))
    # 国家筛选
    if filters.get("country"):
        conditions.append(Farmer.country == filters["country"])
    # 关键词搜索（用户名/昵称/备注）
    if filters.get("keyword"):
        kw = f"%{filters['keyword']}%"
        conditions.append(or_(Farmer.username.like(kw), Farmer.nickname.like(kw), Farmer.remark.like(kw)))
    return conditions


# ==================== 列表与详情 ====================

async def list_farmers(keywords: str = "", search_field: str = "",
                       status: int | None = None, page: int = 1, limit: int = 10) -> dict:
    """分页查询农户列表（含认证状态、绑定产区名）

    Args:
        keywords: 搜索关键词
        search_field: 指定搜索字段 username/nickname/phone/email/company
        status: 状态筛选 0=禁用 1=正常
        page: 页码（从1开始）
        limit: 每页条数

    Returns:
        {"total": int, "page": int, "limit": int, "list": [...]}
    """
    field_map = {
        "username": Farmer.username, "nickname": Farmer.nickname,
        "phone": Farmer.phone, "email": Farmer.email, "company": Farmer.company,
    }
    async with async_session_factory() as db:
        q = select(Farmer)
        if keywords:
            if search_field and search_field in field_map:
                # 指定字段精确搜索
                q = q.where(field_map[search_field].contains(keywords))
            else:
                # 全字段模糊搜索
                q = q.where(
                    Farmer.username.contains(keywords) | Farmer.nickname.contains(keywords) |
                    Farmer.email.contains(keywords) | Farmer.phone.contains(keywords) |
                    Farmer.company.contains(keywords)
                )
        if status is not None:
            q = q.where(Farmer.status == status)

        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        farmers = (await db.execute(
            q.order_by(Farmer.id.desc()).offset((page - 1) * limit).limit(limit)
        )).scalars().all()

        farmer_ids = [f.id for f in farmers]
        area_map = await _query_bound_area_names(db, farmer_ids)

        # 查询当前页农户的实名认证状态（取最新一条记录）
        cert_status_map = {}
        if farmer_ids:
            cert_subq = (
                select(
                    CertificationRecord.farmer_id, CertificationRecord.status,
                    func.row_number().over(
                        partition_by=CertificationRecord.farmer_id,
                        order_by=desc(CertificationRecord.id)
                    ).label("rn")
                ).where(CertificationRecord.farmer_id.in_(farmer_ids)).subquery()
            )
            cert_result = await db.execute(
                select(cert_subq.c.farmer_id, cert_subq.c.status).where(cert_subq.c.rn == 1)
            )
            cert_status_map = {row[0]: row[1] for row in cert_result}

        return {
            "total": total, "page": page, "limit": limit,
            "list": [
                {
                    "id": f.id, "username": f.username, "nickname": f.nickname,
                    "avatar": f.avatar or "", "email": f.email, "phone": f.phone,
                    "company": f.company, "status": f.status,
                    "last_login_ip": f.last_login_ip or "",
                    "create_time": str(f.create_time) if f.create_time else None,
                    "cert_status": cert_status_map.get(f.id, -1),
                    "areas": area_map.get(f.id, [])
                }
                for f in farmers
            ]
        }


async def get_farmer_by_id(farmer_id: int) -> dict | None:
    """查询农户详情（含登录记录、操作日志、绑定产区）

    Returns:
        农户详情 dict 或 None（不存在时）
    """
    async with async_session_factory() as db:
        farmer = (await db.execute(select(Farmer).where(Farmer.id == farmer_id))).scalar_one_or_none()
        if not farmer:
            return None

        # 最近10条登录记录
        logins = (await db.execute(
            select(FarmerLogin).where(FarmerLogin.farmer_id == farmer_id)
            .order_by(FarmerLogin.create_time.desc()).limit(10)
        )).scalars().all()

        # 最近5条操作日志
        op_logs = (await db.execute(
            select(SystemLog).where(SystemLog.rel_id == farmer_id)
            .order_by(desc(SystemLog.id)).limit(5)
        )).scalars().all()

        # 绑定产区名称
        area_map = await _query_bound_area_names(db, [farmer_id])

        return {
            "id": farmer.id, "username": farmer.username,
            "nickname": farmer.nickname or "", "avatar": farmer.avatar or "",
            "email": farmer.email or "", "phone": farmer.phone or "",
            "company": farmer.company or "", "address": farmer.address or "",
            "remark": farmer.remark or "", "country": farmer.country or "中国",
            "language": farmer.language or "中文简体", "status": farmer.status,
            "last_login_ip": farmer.last_login_ip or "",
            "last_action_time": str(farmer.last_action_time) if farmer.last_action_time else None,
            "create_time": str(farmer.create_time) if farmer.create_time else None,
            "areas": area_map.get(farmer_id, []),
            "login_records": [
                {"ip": rec.last_login_ip,
                 "time": str(rec.create_time) if rec.create_time else None}
                for rec in logins
            ],
            "operation_logs": [
                {"id": log.id, "description": log.description or "",
                 "time": str(log.create_time) if log.create_time else None,
                 "ip": log.ip or "", "operator": log.user_name or "system"}
                for log in op_logs
            ]
        }


# ==================== 选项与轻量列表 ====================

async def get_farmer_options() -> list:
    """农户下拉选项 — 返回全量正常农户的 [{id, name, nickname}]"""
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Farmer.id, Farmer.username, Farmer.nickname)
            .where(Farmer.status == 1).order_by(Farmer.id.desc())
        )).all()
        return [{"id": rid, "name": uname, "nickname": nick or ""} for rid, uname, nick in rows]


async def get_simple_farmer_list() -> list:
    """轻量农户列表 — 返回 id/username/phone/email/nickname（最多200条）"""
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Farmer.id, Farmer.username, Farmer.phone, Farmer.email, Farmer.nickname)
            .order_by(Farmer.id.desc()).limit(200)
        )).all()
        return [
            {"id": r[0], "username": r[1], "phone": r[2] or "",
             "email": r[3] or "", "nickname": r[4] or ""}
            for r in rows
        ]


# ==================== 联系方式查询 ====================

async def get_farmer_contact(farmer_id: int) -> dict | None:
    """获取单个农户联系方式（手机/邮箱/昵称），供插件使用"""
    async with async_session_factory() as db:
        row = (await db.execute(
            select(Farmer.id, Farmer.phone, Farmer.email, Farmer.nickname)
            .where(Farmer.id == farmer_id)
        )).first()
        if not row:
            return None
        return {"id": row[0], "phone": row[1] or "", "email": row[2] or "", "nickname": row[3] or ""}


async def list_farmer_contacts(farmer_ids: list[int]) -> list[dict]:
    """批量获取农户联系方式（id/phone/email/nickname）"""
    if not farmer_ids:
        return []
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Farmer.id, Farmer.phone, Farmer.email, Farmer.nickname)
            .where(Farmer.id.in_(farmer_ids))
        )).all()
        return [
            {"id": r[0], "phone": r[1] or "", "email": r[2] or "", "nickname": r[3] or ""}
            for r in rows
        ]


# ==================== 条件筛选 ====================

async def query_farmers_by_filters(filters: dict[str, Any]) -> list[dict]:
    """条件筛选农户列表（从 target_resolver._apply_filters 提取逻辑）

    只返回 status=1 的正常农户。筛选键详见 _build_filter_conditions。
    """
    conditions = _build_filter_conditions(filters)
    async with async_session_factory() as db:
        q = select(Farmer).where(Farmer.status == 1)
        if conditions:
            q = q.where(and_(*conditions))
        farmers = (await db.execute(q)).scalars().all()
        return [
            {"id": f.id, "username": f.username, "phone": f.phone or "", "email": f.email or ""}
            for f in farmers
        ]


async def count_farmers_by_filters(filters: dict[str, Any]) -> int:
    """条件筛选农户计数（与 query_farmers_by_filters 使用相同条件）"""
    conditions = _build_filter_conditions(filters)
    async with async_session_factory() as db:
        q = select(func.count(Farmer.id)).where(Farmer.status == 1)
        if conditions:
            q = q.where(and_(*conditions))
        return (await db.execute(q)).scalar() or 0


# ==================== 增删改 ====================

async def create_farmer(username: str, password: str, nickname: str = "",
                        email: str = "", phone: str = "", company: str = "",
                        db=None, commit: bool = True) -> int:
    """创建农户（password 为已哈希的密码），返回新农户 ID

    Raises:
        sqlalchemy.exc.IntegrityError: 用户名/手机号/邮箱唯一索引冲突
    """
    async def _create(session) -> int:
        farmer = Farmer(
            username=username, password=password,
            nickname=nickname or username,
            email=norm_or_none(email), phone=norm_or_none(phone),
            company=company, status=1
        )
        session.add(farmer)
        await session.flush()
        if commit:
            await session.commit()
        await session.refresh(farmer)
        return farmer.id
    if db is not None:
        return await _create(db)
    async with async_session_factory() as session:
        return await _create(session)


async def update_farmer(farmer_id: int, data: dict) -> bool:
    """更新农户信息（data 为字段名→值的字典），返回是否成功"""
    async with async_session_factory() as db:
        farmer = (await db.execute(select(Farmer).where(Farmer.id == farmer_id))).scalar_one_or_none()
        if not farmer:
            return False
        # 手机号/邮箱空串归一为 NULL（避免与唯一索引冲突）
        for key in ("email", "phone"):
            if key in data:
                data[key] = norm_or_none(data[key])
        for key, value in data.items():
            setattr(farmer, key, value)
        await db.commit()
        return True


async def delete_farmer(farmer_id: int) -> bool:
    """删除农户，返回是否成功"""
    async with async_session_factory() as db:
        farmer = (await db.execute(select(Farmer).where(Farmer.id == farmer_id))).scalar_one_or_none()
        if not farmer:
            return False
        await db.delete(farmer)
        await db.commit()
        return True


async def toggle_farmer_status(farmer_id: int, status: int) -> bool:
    """切换农户状态（0=禁用 1=启用），返回是否成功"""
    async with async_session_factory() as db:
        farmer = (await db.execute(select(Farmer).where(Farmer.id == farmer_id))).scalar_one_or_none()
        if not farmer:
            return False
        await db.execute(update(Farmer).where(Farmer.id == farmer_id).values(status=status))
        await db.commit()
        return True


# ==================== 登录记录与操作日志 ====================

async def get_farmer_login_records(farmer_id: int, limit: int = 10) -> list:
    """查询农户登录记录（默认最近10条）"""
    async with async_session_factory() as db:
        records = (await db.execute(
            select(FarmerLogin).where(FarmerLogin.farmer_id == farmer_id)
            .order_by(FarmerLogin.create_time.desc()).limit(limit)
        )).scalars().all()
        return [
            {"ip": rec.last_login_ip,
             "time": str(rec.create_time) if rec.create_time else None}
            for rec in records
        ]


async def get_farmer_operation_logs(farmer_id: int, page: int = 1, limit: int = 10,
                                    keyword: str = "", operator: str = "",
                                    date_from: str = "", date_to: str = "") -> dict:
    """分页查询农户操作日志（支持关键词/操作人/日期范围筛选）"""
    conditions = [SystemLog.rel_id == farmer_id]
    # 关键词搜索：匹配详情或 IP
    if keyword:
        conditions.append(SystemLog.description.ilike(f"%{keyword}%") | SystemLog.ip.ilike(f"%{keyword}%"))
    # 操作人搜索
    if operator:
        conditions.append(SystemLog.user_name.ilike(f"%{operator}%"))
    # 日期范围
    if date_from:
        try:
            conditions.append(SystemLog.create_time >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            conditions.append(SystemLog.create_time <= datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass

    async with async_session_factory() as db:
        q = select(SystemLog).where(and_(*conditions)).order_by(desc(SystemLog.id))
        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        logs = (await db.execute(q.offset((page - 1) * limit).limit(limit))).scalars().all()
        return {
            "total": total, "page": page, "limit": limit,
            "list": [
                {"id": item.id, "description": item.description or "",
                 "time": str(item.create_time) if item.create_time else None,
                 "ip": item.ip or "", "operator": item.user_name or "system"}
                for item in logs
            ]
        }


# ==================== 产区绑定映射 ====================

async def query_bound_area_names(farmer_ids: list[int]) -> dict[int, list[str]]:
    """批量查询农户绑定的产区名称映射（公开接口，自建会话）

    返回 {farmer_id: [产区名, ...]}，未绑定的农户不在映射中。
    """
    async with async_session_factory() as db:
        return await _query_bound_area_names(db, farmer_ids)


# ==================== 登录认证相关 ====================

async def get_farmer_by_username(username: str) -> dict | None:
    """按用户名查询农户（供登录认证用），返回包含密码哈希的完整信息"""
    async with async_session_factory() as db:
        farmer = (await db.execute(select(Farmer).where(Farmer.username == username))).scalar_one_or_none()
        if not farmer:
            return None
        return {
            "id": farmer.id, "username": farmer.username, "password": farmer.password,
            "nickname": farmer.nickname or "", "email": farmer.email or "",
            "phone": farmer.phone or "", "company": farmer.company or "",
            "status": farmer.status, "avatar": farmer.avatar or "",
            "last_login_ip": farmer.last_login_ip or "",
            "last_action_time": str(farmer.last_action_time) if farmer.last_action_time else None,
            "create_time": str(farmer.create_time) if farmer.create_time else None,
        }


async def farmer_contact_exists(target: str, channel: str) -> bool:
    """检查手机号或邮箱是否已绑定农户，供验证码接口使用。"""
    async with async_session_factory() as db:
        field = Farmer.phone if channel == "sms" else Farmer.email
        row = (await db.execute(select(Farmer.id).where(field == target))).first()
    return row is not None


async def verify_farmer_password(farmer_id: int) -> str:
    """获取农户密码哈希（供密码验证用），不存在时返回空串"""
    async with async_session_factory() as db:
        row = (await db.execute(select(Farmer.password).where(Farmer.id == farmer_id))).first()
        return row[0] if row else ""


async def update_farmer_password(farmer_id: int, password_hash: str) -> bool:
    """更新农户密码（password_hash 为已哈希的密码），返回是否成功"""
    async with async_session_factory() as db:
        await db.execute(
            update(Farmer).where(Farmer.id == farmer_id).values(password=password_hash)
        )
        await db.commit()
        return True


async def create_farmer_login_record(farmer_id: int, ip: str) -> None:
    """创建农户登录记录"""
    async with async_session_factory() as db:
        record = FarmerLogin(farmer_id=farmer_id, last_login_ip=ip, last_action_time=china_now())
        db.add(record)
        await db.commit()


async def update_farmer_login_info(farmer_id: int, ip: str) -> None:
    """更新农户最后登录信息（IP + 时间）"""
    async with async_session_factory() as db:
        await db.execute(
            update(Farmer).where(Farmer.id == farmer_id).values(
                last_login_ip=ip, last_action_time=china_now()
            )
        )
        await db.commit()
