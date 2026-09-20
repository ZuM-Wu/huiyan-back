-- 1.1.2 支持无地块模型测试记录，并保存识别来源类型。
ALTER TABLE hy_plugin_yolo_model_manager_recognition
    MODIFY COLUMN area_id INT UNSIGNED NULL COMMENT '识别时所属产区ID快照，模型测试为空',
    MODIFY COLUMN area_name VARCHAR(128) NULL COMMENT '识别时所属产区名称快照，模型测试为空',
    MODIFY COLUMN plot_id INT UNSIGNED NULL COMMENT '识别地块ID快照，模型测试为空',
    MODIFY COLUMN plot_name VARCHAR(128) NULL COMMENT '识别地块名称快照，模型测试为空',
    ADD COLUMN source_type VARCHAR(32) NOT NULL DEFAULT 'quick_detection' COMMENT '识别来源类型' AFTER source_device_id;

-- 历史接口写入记录没有检测任务ID，按既有外部写入语义回填来源。
UPDATE hy_plugin_yolo_model_manager_recognition
SET source_type = 'external'
WHERE task_id IS NULL;
