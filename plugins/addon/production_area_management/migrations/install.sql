-- 产区管理演示插件建表脚本
-- install() 会先执行本脚本，再播种三条历史日志和三条已完成任务。

CREATE TABLE IF NOT EXISTS hy_plugin_production_area_management_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '日志ID',
    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID',
    area_name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '产区名称快照',
    title VARCHAR(200) NOT NULL COMMENT '日志标题',
    content TEXT NOT NULL COMMENT '日志正文',
    stage VARCHAR(64) NOT NULL DEFAULT '' COMMENT '作物生育阶段',
    weather_json TEXT COMMENT '真实天气快照JSON',
    gdd_json TEXT COMMENT '活动积温与有效积温快照JSON',
    is_latest TINYINT NOT NULL DEFAULT 0 COMMENT '是否最新生成日志 0否 1是',
    is_seed TINYINT NOT NULL DEFAULT 0 COMMENT '是否演示种子数据 0否 1是',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    KEY idx_pam_log_area (area_id),
    KEY idx_pam_log_latest (is_latest),
    KEY idx_pam_log_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区管理演示插件-日志表';

CREATE TABLE IF NOT EXISTS hy_plugin_production_area_management_task (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '任务ID',
    log_id INT NOT NULL DEFAULT 0 COMMENT '来源日志ID',
    title VARCHAR(200) NOT NULL COMMENT '任务标题',
    description TEXT COMMENT '任务说明',
    ai_summary TEXT COMMENT 'AI生成依据摘要',
    priority VARCHAR(16) NOT NULL DEFAULT 'medium' COMMENT '优先级 high/medium/low',
    assignee VARCHAR(64) NOT NULL DEFAULT '' COMMENT '负责人',
    status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '状态 pending/completed',
    plan_time DATETIME NULL COMMENT '计划执行时间',
    completed_time DATETIME NULL COMMENT '完成时间',
    mcp_trace TEXT COMMENT '模拟MCP调用轨迹JSON',
    is_seed TINYINT NOT NULL DEFAULT 0 COMMENT '是否演示种子数据 0否 1是',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    KEY idx_pam_task_log (log_id),
    KEY idx_pam_task_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区管理演示插件-任务表';

CREATE TABLE IF NOT EXISTS hy_plugin_production_area_management_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '反馈ID',
    task_id INT NOT NULL DEFAULT 0 COMMENT '任务ID',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '反馈管理员ID',
    admin_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '反馈人名称',
    result VARCHAR(16) NOT NULL COMMENT '反馈结果 success/partial/failed',
    content TEXT NOT NULL COMMENT '反馈正文',
    metrics_json TEXT COMMENT '反馈指标快照JSON',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '反馈时间',
    KEY idx_pam_feedback_task (task_id),
    KEY idx_pam_feedback_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区管理演示插件-反馈表';
