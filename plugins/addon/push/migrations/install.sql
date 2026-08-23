-- 推送中心 2.0 建表脚本
-- 仅创建新中心表；旧 hy_push_task / hy_push_log 作为历史归档保留

CREATE TABLE IF NOT EXISTS hy_push_center_task (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '任务 ID',
    title VARCHAR(128) NOT NULL COMMENT '推送标题',
    keywords VARCHAR(256) DEFAULT '' COMMENT '关键词/备注',
    content LONGTEXT COMMENT '推送内容（HTML/纯文本）',
    subject VARCHAR(256) DEFAULT '' COMMENT '邮件标题',
    channels JSON NOT NULL COMMENT '渠道配置 JSON',
    target_rule JSON NOT NULL COMMENT '目标规则 JSON',
    schedule_rule JSON NOT NULL COMMENT '调度规则 JSON',
    target_count INT DEFAULT 0 COMMENT '目标农户数',
    send_num INT DEFAULT 0 COMMENT '投递总数',
    success_num INT DEFAULT 0 COMMENT '成功数',
    fail_num INT DEFAULT 0 COMMENT '失败数',
    last_exec_time DATETIME COMMENT '上次执行时间',
    status VARCHAR(16) DEFAULT 'Wait' COMMENT '状态:Draft/Wait/Exec/Suspended/Finish/Expired',
    admin_id INT DEFAULT 0 COMMENT '创建人 ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_center_status (status),
    INDEX idx_center_update (update_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='推送中心任务表';

CREATE TABLE IF NOT EXISTS hy_push_center_delivery_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '日志 ID',
    task_id INT NOT NULL COMMENT '推送中心任务 ID',
    farmer_id INT NOT NULL COMMENT '农户 ID',
    username VARCHAR(128) DEFAULT '' COMMENT '农户用户名',
    channel VARCHAR(16) NOT NULL COMMENT '渠道:inbox/sms/email',
    status VARCHAR(16) DEFAULT 'Pending' COMMENT '状态:Pending/Success/Failed',
    reason TEXT COMMENT '失败原因',
    notification_log_id BIGINT COMMENT '通知中心日志 ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_center_delivery_task (task_id),
    INDEX idx_center_delivery_farmer (farmer_id),
    INDEX idx_center_delivery_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='推送中心投递日志表';
