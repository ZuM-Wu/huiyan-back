# -*- coding: utf-8 -*-
"""
App管理插件数据模型

表定义规范：
- 表名前缀 hy_plugin_app_manage_{entity}
- 继承 core.db.base.Base
- 每个字段带中文 comment

注意：插件业务表由 plugin.install() 通过 migrations/install.sql 建表，
此 ORM 模型仅用于业务代码中的查询与序列化，不参与系统启动时的 create_all。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text

from core.db.base import Base


class AppManageVersion(Base):
    """App版本表 — 每次发版一行，App端按 version_code 数值比较"""

    __tablename__ = "hy_plugin_app_manage_version"
    __table_args__ = {"comment": "App管理插件-App版本表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="版本记录ID")
    version_name = Column(String(32), nullable=False, comment="版本名(如1.2.0)")
    version_code = Column(Integer, nullable=False, unique=True, comment="版本号(App端数值比较用)")
    build_number = Column(String(32), nullable=False, default="", comment="内部构建号")
    apk_filename = Column(String(64), nullable=False, comment="APK磁盘文件名(UUID重命名)")
    apk_size = Column(BigInteger, nullable=False, default=0, comment="APK文件大小(字节)")
    apk_md5 = Column(String(32), nullable=False, default="", comment="APK文件MD5(App端下载校验)")
    changelog = Column(Text, comment="更新日志")
    update_policy = Column(Integer, nullable=False, default=1, comment="更新策略 0可忽略 1提示可稍后 2强制")
    status = Column(Integer, nullable=False, default=1, comment="状态 1发布 0下架")
    admin_id = Column(Integer, nullable=False, default=0, comment="操作管理员ID")
    create_time = Column(DateTime, default=datetime.now, comment="创建时间")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class AppManageAd(Base):
    """开屏广告表 — 业务上仅使用 id=1 单行，管理端只更新不新增"""

    __tablename__ = "hy_plugin_app_manage_ad"
    __table_args__ = {"comment": "App管理插件-开屏广告表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="广告ID(业务上仅使用id=1单行)")
    image_filename = Column(String(64), nullable=False, default="", comment="广告图磁盘文件名(UUID重命名)")
    cache_key = Column(String(64), nullable=False, default="", comment="素材缓存标识(App端比对判断是否重新下载)")
    link_url = Column(String(500), nullable=False, default="", comment="点击跳转URL(空则不跳转)")
    start_time = Column(DateTime, comment="投放开始时间(NULL=立即)")
    end_time = Column(DateTime, comment="投放结束时间(NULL=不限)")
    duration = Column(Integer, nullable=False, default=3, comment="开屏展示秒数")
    enabled = Column(Integer, nullable=False, default=0, comment="是否启用 0禁用 1启用")
    admin_id = Column(Integer, nullable=False, default=0, comment="最后操作管理员ID")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class AppManageNotice(Base):
    """App公告表 — 独立于核心 notice 模块的App专用公告"""

    __tablename__ = "hy_plugin_app_manage_notice"
    __table_args__ = {"comment": "App管理插件-App公告表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="公告ID")
    title = Column(String(200), nullable=False, comment="公告标题")
    content = Column(Text, comment="公告内容")
    is_popup = Column(Integer, nullable=False, default=0, comment="是否弹窗提示 0否 1是")
    start_time = Column(DateTime, comment="生效开始时间(NULL=立即)")
    end_time = Column(DateTime, comment="生效结束时间(NULL=永久)")
    enabled = Column(Integer, nullable=False, default=1, comment="是否启用 0禁用 1启用")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序值(越小越靠前)")
    admin_id = Column(Integer, nullable=False, default=0, comment="操作管理员ID")
    create_time = Column(DateTime, default=datetime.now, comment="创建时间")
    update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
