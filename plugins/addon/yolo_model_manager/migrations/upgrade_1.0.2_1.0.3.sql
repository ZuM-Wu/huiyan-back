-- 1.0.3 增加快捷检测任务关联、来源设备和原图尺寸快照。
ALTER TABLE hy_plugin_yolo_model_manager_recognition
    ADD COLUMN task_id BIGINT UNSIGNED NULL COMMENT '检测任务ID' AFTER model_version,
    ADD COLUMN source_device_id INT UNSIGNED NULL COMMENT '来源硬件设备ID快照' AFTER task_id,
    ADD COLUMN image_identifier VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源图片标识快照' AFTER source_device_id,
    ADD COLUMN image_width INT UNSIGNED NULL COMMENT '识别原图宽度像素' AFTER image_url,
    ADD COLUMN image_height INT UNSIGNED NULL COMMENT '识别原图高度像素' AFTER image_width,
    ADD UNIQUE KEY uk_yolo_recognition_task (task_id),
    ADD INDEX idx_yolo_recognition_device_time (source_device_id, recognized_at);
