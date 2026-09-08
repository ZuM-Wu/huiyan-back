-- 1.0.5 为快捷识别记录增加持久化的标注结果图地址，旧记录保持为空。
ALTER TABLE hy_plugin_yolo_model_manager_recognition
    ADD COLUMN annotated_image_url VARCHAR(1000) NOT NULL DEFAULT ''
        COMMENT '标注结果图地址' AFTER image_url;
