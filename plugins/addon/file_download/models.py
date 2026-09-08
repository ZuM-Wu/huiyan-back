# -*- coding: utf-8 -*-
"""
文件下载插件数据模型

表定义规范：
- 表名前缀 hy_plugin_file_download_{entity}
- 继承 core.db.base.Base
- 每个字段带中文 comment

注意：插件业务表由 plugin.install() 通过 migrations/install.sql 建表，
此 ORM 模型仅用于业务代码中的查询与序列化，不参与系统启动时的 create_all。
"""
from core.time_utils import china_now
from sqlalchemy import Column, DateTime, Integer, String

from core.db.base import Base


class FileDownloadFolder(Base):
    """文件夹表 — 文件的一级分组容器"""

    __tablename__ = "hy_plugin_file_download_folder"
    __table_args__ = {"comment": "文件下载插件-文件夹表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="文件夹ID")
    name = Column(String(100), nullable=False, comment="文件夹名称")
    is_default = Column(Integer, nullable=False, default=0, comment="是否默认文件夹 0否 1是")
    admin_id = Column(Integer, nullable=False, default=0, comment="最后操作管理员ID")
    create_time = Column(DateTime, default=china_now, comment="创建时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class FileDownloadFile(Base):
    """文件表 — 管理员上传的分发文件"""

    __tablename__ = "hy_plugin_file_download_file"
    __table_args__ = {"comment": "文件下载插件-文件表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="文件ID")
    folder_id = Column(Integer, nullable=False, default=0, index=True, comment="所属文件夹ID")
    name = Column(String(200), nullable=False, comment="显示名称")
    filename = Column(String(200), nullable=False, comment="磁盘文件名(UUID重命名)")
    origin_name = Column(String(200), nullable=False, comment="原始文件名(下载时还原)")
    filetype = Column(String(50), nullable=False, default="", comment="文件扩展名")
    filesize = Column(Integer, nullable=False, default=0, comment="文件大小(字节)")
    visible_range = Column(
        String(20), nullable=False, default="all",
        comment="可见范围 all:所有农户 area:指定产区绑定农户",
    )
    hidden = Column(Integer, nullable=False, default=0, comment="是否隐藏 0显示 1隐藏")
    download_count = Column(Integer, nullable=False, default=0, comment="下载次数")
    description = Column(String(1000), nullable=False, default="", comment="文件描述")
    admin_id = Column(Integer, nullable=False, default=0, comment="上传管理员ID")
    create_time = Column(DateTime, default=china_now, comment="上传时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class FileDownloadArea(Base):
    """文件-产区关联表 — 仅 visible_range='area' 时使用"""

    __tablename__ = "hy_plugin_file_download_area"
    __table_args__ = {"comment": "文件下载插件-文件产区关联表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="关联ID")
    file_id = Column(Integer, nullable=False, default=0, index=True, comment="文件ID")
    area_id = Column(Integer, nullable=False, default=0, index=True, comment="产区ID")
