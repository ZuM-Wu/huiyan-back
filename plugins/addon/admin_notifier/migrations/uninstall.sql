-- 邮件通知管理员插件 — 卸载 SQL
-- hy_task_log 表由系统核心管理，卸载插件时不删除

DROP TABLE IF EXISTS `hy_task_alert_config`;
