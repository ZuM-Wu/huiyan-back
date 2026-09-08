-- 1.0.1 为已安装模型补充类别标签，旧记录由 upgrade() 尽量补取。
ALTER TABLE hy_plugin_yolo_model_manager_model
    ADD COLUMN labels JSON NULL COMMENT '模型类别标签JSON数组' AFTER sha256;

UPDATE hy_plugin_yolo_model_manager_model
SET labels = JSON_ARRAY()
WHERE labels IS NULL;

ALTER TABLE hy_plugin_yolo_model_manager_model
    MODIFY COLUMN labels JSON NOT NULL COMMENT '模型类别标签JSON数组',
    COMMENT='智能识别插件-模型表';

ALTER TABLE hy_plugin_yolo_model_manager_binding
    COMMENT='智能识别插件-模型地块绑定表';
