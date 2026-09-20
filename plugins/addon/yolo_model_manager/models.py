# -*- coding: utf-8 -*-
"""智能识别插件的模型、地块绑定与识别记录数据模型。"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)

from core.db.base import Base
from core.time_utils import china_now


class YoloModel(Base):
    """管理员上传的 YOLO 模型文件及其可读元数据。"""

    __tablename__ = "hy_plugin_yolo_model_manager_model"
    __table_args__ = {"comment": "智能识别插件-模型表"}

    id = Column(Integer, primary_key=True, autoincrement=True, comment="模型ID")
    name = Column(String(128), nullable=False, comment="模型显示名称")
    version = Column(String(64), nullable=False, default="", comment="模型业务版本")
    description = Column(String(1000), nullable=False, default="", comment="模型说明")
    default_confidence = Column(
        Numeric(5, 4), nullable=False, default=0.25, comment="模型默认识别置信度"
    )
    filename = Column(String(128), nullable=False, comment="私有目录磁盘文件名")
    origin_name = Column(String(255), nullable=False, comment="上传时原始文件名")
    file_format = Column(String(16), nullable=False, comment="模型文件格式")
    file_size = Column(BigInteger, nullable=False, default=0, comment="模型文件大小字节数")
    sha256 = Column(String(64), nullable=False, comment="模型文件SHA-256摘要")
    labels = Column(JSON, nullable=False, default=list, comment="模型类别标签JSON数组")
    admin_id = Column(Integer, nullable=False, default=0, comment="上传管理员ID")
    create_time = Column(DateTime, default=china_now, comment="上传时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class YoloModelPlotBinding(Base):
    """模型与地块绑定；地块唯一约束保证每个地块最多绑定一个模型。"""

    __tablename__ = "hy_plugin_yolo_model_manager_binding"
    __table_args__ = (
        UniqueConstraint("plot_id", name="uk_yolo_model_manager_plot"),
        Index("idx_yolo_model_manager_model", "model_id"),
        {"comment": "智能识别插件-模型地块绑定表"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="绑定ID")
    model_id = Column(Integer, nullable=False, comment="模型ID")
    plot_id = Column(Integer, nullable=False, comment="地块ID")
    admin_id = Column(Integer, nullable=False, default=0, comment="最后绑定管理员ID")
    create_time = Column(DateTime, default=china_now, comment="绑定时间")
    update_time = Column(DateTime, default=china_now, onupdate=china_now, comment="更新时间")


class RecognitionRecord(Base):
    """识别完成后的不可变业务快照，不依赖模型和地块后续存续状态。"""

    __tablename__ = "hy_plugin_yolo_model_manager_recognition"
    __table_args__ = (
        UniqueConstraint("task_id", name="uk_yolo_recognition_task"),
        Index("idx_yolo_recognition_plot_time", "plot_id", "recognized_at"),
        Index("idx_yolo_recognition_model_time", "model_id", "recognized_at"),
        Index("idx_yolo_recognition_device_time", "source_device_id", "recognized_at"),
        Index("idx_yolo_recognition_time", "recognized_at"),
        {"comment": "智能识别插件-识别记录表"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="识别记录ID")
    area_id = Column(Integer, nullable=True, comment="识别时所属产区ID快照，模型测试为空")
    area_name = Column(String(128), nullable=True, comment="识别时所属产区名称快照，模型测试为空")
    plot_id = Column(Integer, nullable=True, comment="识别地块ID快照，模型测试为空")
    plot_name = Column(String(128), nullable=True, comment="识别地块名称快照，模型测试为空")
    model_id = Column(Integer, nullable=False, comment="识别模型ID快照")
    model_name = Column(String(128), nullable=False, comment="识别模型名称快照")
    model_version = Column(String(64), nullable=False, default="", comment="识别模型版本快照")
    task_id = Column(BigInteger, nullable=True, comment="检测任务ID")
    source_device_id = Column(Integer, nullable=True, comment="来源硬件设备ID快照")
    source_type = Column(String(32), nullable=False, default="quick_detection", comment="识别来源类型")
    image_identifier = Column(String(128), nullable=False, default="", comment="来源图片标识快照")
    image_url = Column(String(1000), nullable=False, default="", comment="识别原图地址")
    annotated_image_url = Column(
        String(1000), nullable=False, default="", comment="标注结果图地址"
    )
    image_width = Column(Integer, nullable=True, comment="识别原图宽度像素")
    image_height = Column(Integer, nullable=True, comment="识别原图高度像素")
    detections = Column(JSON, nullable=False, default=list, comment="识别目标明细JSON数组")
    detection_count = Column(Integer, nullable=False, default=0, comment="识别目标数量")
    max_confidence = Column(Numeric(6, 5), nullable=True, comment="最高识别置信度")
    recognized_at = Column(DateTime, nullable=False, default=china_now, comment="业务识别时间")
    admin_id = Column(Integer, nullable=False, default=0, comment="写入管理员ID")
    create_time = Column(DateTime, nullable=False, default=china_now, comment="记录创建时间")
