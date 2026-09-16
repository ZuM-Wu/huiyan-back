-- 产区管理插件建表脚本
-- 当前版本不播种演示数据，日志由真实事实同步生成。

CREATE TABLE IF NOT EXISTS hy_plugin_production_area_management_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '日志ID',
    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID',
    area_name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '产区名称快照',
    fact_date DATE NULL COMMENT '事实日期',
    title VARCHAR(200) NOT NULL DEFAULT '' COMMENT '日志标题',
    content TEXT NOT NULL COMMENT '真实事实正文',
    stage VARCHAR(64) NOT NULL DEFAULT '' COMMENT '作物生育阶段',
    weather_json TEXT COMMENT '逐日天气事实JSON',
    gdd_json TEXT COMMENT '截至事实日期的积温JSON',
    recommendation TEXT COMMENT '整改建议',
    is_latest TINYINT NOT NULL DEFAULT 0 COMMENT '是否最新事实日期 0否 1是',
    is_seed TINYINT NOT NULL DEFAULT 0 COMMENT '是否旧版演示种子数据 0否 1是',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录生成时间',
    KEY idx_pam_log_area (area_id),
    KEY idx_pam_log_latest (is_latest),
    KEY idx_pam_log_fact_date (fact_date),
    KEY idx_pam_log_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区管理-按日事实日志表';

CREATE TABLE IF NOT EXISTS hy_plugin_production_area_management_task (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '任务ID',
    log_id INT NOT NULL DEFAULT 0 COMMENT '来源日志ID',
    title VARCHAR(200) NOT NULL COMMENT '整改任务标题',
    description TEXT COMMENT '整改任务说明',
    ai_summary TEXT COMMENT '历史兼容摘要',
    priority VARCHAR(16) NOT NULL DEFAULT 'medium' COMMENT '优先级 high/medium/low',
    assignee VARCHAR(64) NOT NULL DEFAULT '' COMMENT '负责人',
    status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '状态 pending/completed',
    plan_time DATETIME NULL COMMENT '计划执行时间',
    completed_time DATETIME NULL COMMENT '完成时间',
    mcp_trace TEXT COMMENT '历史兼容调用轨迹JSON',
    is_seed TINYINT NOT NULL DEFAULT 0 COMMENT '是否旧版演示种子数据 0否 1是',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    KEY idx_pam_task_log (log_id),
    KEY idx_pam_task_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区管理-整改任务表';

CREATE TABLE IF NOT EXISTS hy_plugin_production_area_management_feedback (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '反馈ID',
    task_id INT NOT NULL DEFAULT 0 COMMENT '任务ID',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '反馈管理员ID',
    admin_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '反馈人名称',
    result VARCHAR(16) NOT NULL COMMENT '反馈结果 success/partial/failed',
    content TEXT NOT NULL COMMENT '现场文字说明',
    images_json TEXT COMMENT '现场图片稳定地址JSON数组',
    metrics_json TEXT COMMENT '历史兼容指标JSON',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '反馈时间',
    KEY idx_pam_feedback_task (task_id),
    KEY idx_pam_feedback_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产区管理-任务反馈表';
