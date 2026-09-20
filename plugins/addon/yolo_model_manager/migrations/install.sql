-- 智能识别插件建表脚本，使用 IF NOT EXISTS 保证安装幂等。

CREATE TABLE IF NOT EXISTS hy_plugin_yolo_model_manager_model (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '模型ID',
    name VARCHAR(128) NOT NULL COMMENT '模型显示名称',
    version VARCHAR(64) NOT NULL DEFAULT '' COMMENT '模型业务版本',
    description VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '模型说明',
    default_confidence DECIMAL(5, 4) NOT NULL DEFAULT 0.2500 COMMENT '模型默认识别置信度',
    filename VARCHAR(128) NOT NULL COMMENT '私有目录磁盘文件名',
    origin_name VARCHAR(255) NOT NULL COMMENT '上传时原始文件名',
    file_format VARCHAR(16) NOT NULL COMMENT '模型文件格式',
    file_size BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '模型文件大小字节数',
    sha256 CHAR(64) NOT NULL COMMENT '模型文件SHA-256摘要',
    labels JSON NOT NULL COMMENT '模型类别标签JSON数组',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '上传管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_yolo_model_format (file_format),
    INDEX idx_yolo_model_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能识别插件-模型表';

CREATE TABLE IF NOT EXISTS hy_plugin_yolo_model_manager_binding (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '绑定ID',
    model_id INT UNSIGNED NOT NULL COMMENT '模型ID',
    plot_id INT UNSIGNED NOT NULL COMMENT '地块ID',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '最后绑定管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '绑定时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_yolo_model_manager_plot (plot_id),
    INDEX idx_yolo_model_manager_model (model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能识别插件-模型地块绑定表';

CREATE TABLE IF NOT EXISTS hy_plugin_yolo_model_manager_recognition (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '识别记录ID',
    area_id INT UNSIGNED NULL COMMENT '识别时所属产区ID快照，模型测试为空',
    area_name VARCHAR(128) NULL COMMENT '识别时所属产区名称快照，模型测试为空',
    plot_id INT UNSIGNED NULL COMMENT '识别地块ID快照，模型测试为空',
    plot_name VARCHAR(128) NULL COMMENT '识别地块名称快照，模型测试为空',
    model_id INT UNSIGNED NOT NULL COMMENT '识别模型ID快照',
    model_name VARCHAR(128) NOT NULL COMMENT '识别模型名称快照',
    model_version VARCHAR(64) NOT NULL DEFAULT '' COMMENT '识别模型版本快照',
    task_id BIGINT UNSIGNED NULL COMMENT '检测任务ID',
    source_device_id INT UNSIGNED NULL COMMENT '来源硬件设备ID快照',
    source_type VARCHAR(32) NOT NULL DEFAULT 'quick_detection' COMMENT '识别来源类型',
    image_identifier VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源图片标识快照',
    image_url VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '识别原图地址',
    annotated_image_url VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '标注结果图地址',
    image_width INT UNSIGNED NULL COMMENT '识别原图宽度像素',
    image_height INT UNSIGNED NULL COMMENT '识别原图高度像素',
    detections JSON NOT NULL COMMENT '识别目标明细JSON数组',
    detection_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '识别目标数量',
    max_confidence DECIMAL(6, 5) NULL COMMENT '最高识别置信度',
    recognized_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '业务识别时间',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '写入管理员ID',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    UNIQUE KEY uk_yolo_recognition_task (task_id),
    INDEX idx_yolo_recognition_plot_time (plot_id, recognized_at),
    INDEX idx_yolo_recognition_model_time (model_id, recognized_at),
    INDEX idx_yolo_recognition_device_time (source_device_id, recognized_at),
    INDEX idx_yolo_recognition_time (recognized_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能识别插件-识别记录表';
