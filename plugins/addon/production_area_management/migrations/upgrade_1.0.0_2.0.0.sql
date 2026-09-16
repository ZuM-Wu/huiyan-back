-- 产区管理插件 1.0.0 -> 2.0.0
-- 仅清理旧版演示种子链路，保留所有非种子业务数据。
-- 可重复执行：新增结构先探测 information_schema 再执行（MySQL 8 不支持 ADD COLUMN IF NOT EXISTS），
-- 列定义、中文备注与表备注在本脚本内收敛到 install.sql 的最终形态，避免升级库与全新安装库结构漂移。

-- 1. 日志表新增事实日期列
SET @pam_ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'hy_plugin_production_area_management_log'
          AND COLUMN_NAME = 'fact_date') = 0,
    'ALTER TABLE hy_plugin_production_area_management_log ADD COLUMN fact_date DATE NULL COMMENT ''事实日期'' AFTER area_name',
    'DO 0');
PREPARE pam_stmt FROM @pam_ddl;
EXECUTE pam_stmt;
DEALLOCATE PREPARE pam_stmt;

-- 2. 日志表新增整改建议列
SET @pam_ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'hy_plugin_production_area_management_log'
          AND COLUMN_NAME = 'recommendation') = 0,
    'ALTER TABLE hy_plugin_production_area_management_log ADD COLUMN recommendation TEXT NULL COMMENT ''整改建议'' AFTER gdd_json',
    'DO 0');
PREPARE pam_stmt FROM @pam_ddl;
EXECUTE pam_stmt;
DEALLOCATE PREPARE pam_stmt;

-- 3. 日志表新增事实日期索引
SET @pam_ddl := IF((SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'hy_plugin_production_area_management_log'
          AND INDEX_NAME = 'idx_pam_log_fact_date') = 0,
    'ALTER TABLE hy_plugin_production_area_management_log ADD KEY idx_pam_log_fact_date (fact_date)',
    'DO 0');
PREPARE pam_stmt FROM @pam_ddl;
EXECUTE pam_stmt;
DEALLOCATE PREPARE pam_stmt;

-- 4. 反馈表新增现场图片列
SET @pam_ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'hy_plugin_production_area_management_feedback'
          AND COLUMN_NAME = 'images_json') = 0,
    'ALTER TABLE hy_plugin_production_area_management_feedback ADD COLUMN images_json TEXT NULL COMMENT ''现场图片稳定地址JSON数组'' AFTER content',
    'DO 0');
PREPARE pam_stmt FROM @pam_ddl;
EXECUTE pam_stmt;
DEALLOCATE PREPARE pam_stmt;

-- 5. 日志表列定义与中文备注收敛
ALTER TABLE hy_plugin_production_area_management_log
    MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    MODIFY COLUMN area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID',
    MODIFY COLUMN area_name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '产区名称快照',
    MODIFY COLUMN fact_date DATE NULL COMMENT '事实日期',
    MODIFY COLUMN title VARCHAR(200) NOT NULL DEFAULT '' COMMENT '日志标题',
    MODIFY COLUMN content TEXT NOT NULL COMMENT '真实事实正文',
    MODIFY COLUMN stage VARCHAR(64) NOT NULL DEFAULT '' COMMENT '作物生育阶段',
    MODIFY COLUMN weather_json TEXT NULL COMMENT '逐日天气事实JSON',
    MODIFY COLUMN gdd_json TEXT NULL COMMENT '截至事实日期的积温JSON',
    MODIFY COLUMN recommendation TEXT NULL COMMENT '整改建议',
    MODIFY COLUMN is_latest TINYINT NOT NULL DEFAULT 0 COMMENT '是否最新事实日期 0否 1是',
    MODIFY COLUMN is_seed TINYINT NOT NULL DEFAULT 0 COMMENT '是否旧版演示种子数据 0否 1是',
    MODIFY COLUMN create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录生成时间';

-- 6. 整改任务表列定义与中文备注收敛
ALTER TABLE hy_plugin_production_area_management_task
    MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT COMMENT '任务ID',
    MODIFY COLUMN log_id INT NOT NULL DEFAULT 0 COMMENT '来源日志ID',
    MODIFY COLUMN title VARCHAR(200) NOT NULL COMMENT '整改任务标题',
    MODIFY COLUMN description TEXT NULL COMMENT '整改任务说明',
    MODIFY COLUMN ai_summary TEXT NULL COMMENT '历史兼容摘要',
    MODIFY COLUMN priority VARCHAR(16) NOT NULL DEFAULT 'medium' COMMENT '优先级 high/medium/low',
    MODIFY COLUMN assignee VARCHAR(64) NOT NULL DEFAULT '' COMMENT '负责人',
    MODIFY COLUMN status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '状态 pending/completed',
    MODIFY COLUMN plan_time DATETIME NULL COMMENT '计划执行时间',
    MODIFY COLUMN completed_time DATETIME NULL COMMENT '完成时间',
    MODIFY COLUMN mcp_trace TEXT NULL COMMENT '历史兼容调用轨迹JSON',
    MODIFY COLUMN is_seed TINYINT NOT NULL DEFAULT 0 COMMENT '是否旧版演示种子数据 0否 1是',
    MODIFY COLUMN create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    MODIFY COLUMN update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间';

-- 7. 任务反馈表列定义与中文备注收敛
ALTER TABLE hy_plugin_production_area_management_feedback
    MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT COMMENT '反馈ID',
    MODIFY COLUMN task_id INT NOT NULL DEFAULT 0 COMMENT '任务ID',
    MODIFY COLUMN admin_id INT NOT NULL DEFAULT 0 COMMENT '反馈管理员ID',
    MODIFY COLUMN admin_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '反馈人名称',
    MODIFY COLUMN result VARCHAR(16) NOT NULL COMMENT '反馈结果 success/partial/failed',
    MODIFY COLUMN content TEXT NOT NULL COMMENT '现场文字说明',
    MODIFY COLUMN images_json TEXT NULL COMMENT '现场图片稳定地址JSON数组',
    MODIFY COLUMN metrics_json TEXT NULL COMMENT '历史兼容指标JSON',
    MODIFY COLUMN create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '反馈时间';

-- 8. 表备注收敛
ALTER TABLE hy_plugin_production_area_management_log COMMENT='产区管理-按日事实日志表';
ALTER TABLE hy_plugin_production_area_management_task COMMENT='产区管理-整改任务表';
ALTER TABLE hy_plugin_production_area_management_feedback COMMENT='产区管理-任务反馈表';

-- 9. 清理旧版演示种子链路（三条 DELETE 均带 WHERE，可重复执行）
DELETE feedback_row
FROM hy_plugin_production_area_management_feedback AS feedback_row
INNER JOIN hy_plugin_production_area_management_task AS task_row
    ON task_row.id = feedback_row.task_id
WHERE task_row.is_seed = 1;
DELETE FROM hy_plugin_production_area_management_task WHERE is_seed = 1;
DELETE FROM hy_plugin_production_area_management_log WHERE is_seed = 1;
