-- 企业微信通知插件 3.0.0 建表 SQL
-- 动作目录由插件安装钩子幂等写入，全部默认关闭。

CREATE TABLE IF NOT EXISTS `hy_wecom_webhook_action` (
    `id` INT NOT NULL AUTO_INCREMENT COMMENT '配置ID',
    `action_key` VARCHAR(64) NOT NULL COMMENT '管理员通知动作标识',
    `action_name` VARCHAR(128) DEFAULT '' COMMENT '动作名称',
    `action_type` VARCHAR(32) DEFAULT 'other' COMMENT '动作分类',
    `enabled` TINYINT DEFAULT 0 COMMENT '是否启用:0否 1是',
    `webhook_url` VARCHAR(512) DEFAULT '' COMMENT '动作级Webhook地址，留空继承全局',
    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_wecom_action_key` (`action_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业微信管理员通知动作配置表';

CREATE TABLE IF NOT EXISTS `hy_wecom_webhook_log` (
    `id` INT NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    `action_key` VARCHAR(64) DEFAULT '' COMMENT '通知动作标识',
    `msgtype` VARCHAR(32) DEFAULT 'template_card' COMMENT '消息类型',
    `content` TEXT COMMENT '实际发送的消息内容（截断前500字符）',
    `status` TINYINT DEFAULT 0 COMMENT '发送状态:0=失败 1=成功',
    `error_msg` VARCHAR(512) DEFAULT '' COMMENT '错误信息',
    `msg_id` VARCHAR(128) DEFAULT '' COMMENT '企业微信返回的消息ID',
    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    PRIMARY KEY (`id`),
    KEY `idx_action_key` (`action_key`),
    KEY `idx_create_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业微信发送日志表';
