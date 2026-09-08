-- 1.0.2 新增不可变识别记录，名称快照保证地块或模型变更后历史仍可读。
CREATE TABLE IF NOT EXISTS hy_plugin_yolo_model_manager_recognition (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '识别记录ID',
    area_id INT UNSIGNED NOT NULL COMMENT '识别时所属产区ID快照',
    area_name VARCHAR(128) NOT NULL COMMENT '识别时所属产区名称快照',
    plot_id INT UNSIGNED NOT NULL COMMENT '识别地块ID快照',
    plot_name VARCHAR(128) NOT NULL COMMENT '识别地块名称快照',
    model_id INT UNSIGNED NOT NULL COMMENT '识别模型ID快照',
    model_name VARCHAR(128) NOT NULL COMMENT '识别模型名称快照',
    model_version VARCHAR(64) NOT NULL DEFAULT '' COMMENT '识别模型版本快照',
    image_url VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '识别原图地址',
    detections JSON NOT NULL COMMENT '识别目标明细JSON数组',
    detection_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '识别目标数量',
    max_confidence DECIMAL(6, 5) NULL COMMENT '最高识别置信度',
    recognized_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '业务识别时间',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '写入管理员ID',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    INDEX idx_yolo_recognition_plot_time (plot_id, recognized_at),
    INDEX idx_yolo_recognition_model_time (model_id, recognized_at),
    INDEX idx_yolo_recognition_time (recognized_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能识别插件-识别记录表';

ALTER TABLE hy_plugin_yolo_model_manager_model
    COMMENT='智能识别插件-模型表';

ALTER TABLE hy_plugin_yolo_model_manager_binding
    COMMENT='智能识别插件-模型地块绑定表';
