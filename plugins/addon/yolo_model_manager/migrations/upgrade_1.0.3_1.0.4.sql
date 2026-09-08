-- 1.0.4 为每个模型增加可配置的默认识别置信度。
ALTER TABLE hy_plugin_yolo_model_manager_model
    ADD COLUMN default_confidence DECIMAL(5, 4) NOT NULL DEFAULT 0.2500
        COMMENT '模型默认识别置信度' AFTER description;
