"""
个人 API Key 模型
用于 MCP 服务的 Bearer Key 鉴权：管理员/农户在个人中心创建个人 Key，
Key 引用其本人在现有权限体系中的权限（管理员走 RBAC，农户按身份归属）。

安全约定:
- 鉴权热路径仅按 sha256 哈希（key_hash）精确查找，哈希列唯一索引
- key_plain 存密钥明文，仅用于个人中心列表可视化查看与复制（产品要求）
- prefix 存明文前 8 位，用于存量无明文密钥的回退展示
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class ApiKeyModel(Base):
    """个人 API Key 表"""
    __tablename__ = "hy_api_key"
    __table_args__ = (
        # key_hash 唯一索引（鉴权热路径按哈希精确查找）
        Index("uk_api_key_hash", "key_hash", unique=True),
        # 用户维度联合索引（个人中心列表查询）
        Index("idx_api_key_user", "user_type", "user_id"),
        {"comment": "个人API密钥表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    user_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="用户体系: admin=管理员, farmer=农户")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属用户ID（hy_admin.id 或 hy_farmer.id）")
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="密钥备注名")
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="密钥 sha256 哈希（鉴权查找用）")
    key_plain: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="密钥明文（列表可视化展示用）")
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="", comment="明文前8位（存量密钥回退展示用）")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="状态: 1=启用, 2=已吊销")
    last_used_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后使用时间（鉴权命中时节流回写）")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
