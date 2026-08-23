-- 邮件通知管理员插件 — 建表 SQL
-- hy_task_log 表由系统核心管理（core/db/task_log.py），此处仅建插件配置表

CREATE TABLE IF NOT EXISTS `hy_task_alert_config` (
    `id` INT NOT NULL AUTO_INCREMENT COMMENT '配置ID',
    `task_name` VARCHAR(64) NOT NULL COMMENT '任务名称（英文标识）',
    `task_title` VARCHAR(128) DEFAULT '' COMMENT '任务显示名称（中文）',
    `task_type` VARCHAR(32) DEFAULT 'system' COMMENT '任务类型: system/weather/notice/plugin',
    `notify_enabled` TINYINT DEFAULT 0 COMMENT '是否启用邮件通知',
    `notify_channel` VARCHAR(16) DEFAULT 'email' COMMENT '通知渠道: email/sms',
    `notify_interface` VARCHAR(64) DEFAULT '' COMMENT '通知接口标识（如 mail_smtp）',
    `admin_ids` VARCHAR(256) DEFAULT '' COMMENT '接收通知的管理员ID列表（逗号分隔）',
    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_name` (`task_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务告警配置表';
