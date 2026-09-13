"""
文件存储日志模型
记录每次文件上传的存储归属信息，用于对象存储切换时的数据追踪与迁移评估
对标 ZJMF file_log 表（FileLogModel.php），字段精简并补索引
"""
from datetime import datetime
from core.time_utils import china_now
from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class FileLogModel(Base):
    """文件存储日志表"""
    __tablename__ = "hy_file_log"
    __table_args__ = (
        # save_name 普通索引；同名文件可存在于不同目录
        Index("idx_file_log_save_name", "save_name"),
        # local_path 是本地 upload/ 下的稳定相对路径；使用摘要避免 utf8mb4 长路径超过旧 InnoDB 索引上限。
        UniqueConstraint("local_path_hash", name="uk_file_log_local_path"),
        # uuid 普通索引（签名URL查询）
        Index("idx_file_log_uuid", "uuid"),
        # oss_method 普通索引（迁移评估统计）
        Index("idx_file_log_oss_method", "oss_method"),
        {"comment": "文件存储日志表"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    uuid: Mapped[str] = mapped_column(String(64), nullable=False, comment="文件唯一标识（用于签名URL的fid）")
    save_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="存储文件名（UUID重命名后）")
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None, comment="本地 upload/ 下的相对路径")
    local_path_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, comment="本地相对路径SHA-256摘要")
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, default="", comment="对象存储中的真实对象键")
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="原始文件名")
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="", comment="扩展名（含点号）")
    oss_method: Mapped[str] = mapped_column(String(64), nullable=False, default="local_oss", comment="存储插件标识")
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="", comment="访问URL")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="文件字节数")
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="上传管理员ID")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="admin", comment="来源：admin/farmer/system")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=china_now, comment="创建时间")
