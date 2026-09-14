-- 企业微信通知插件 1.0.0 -> 2.0.0 统一动作配置迁移
CREATE TABLE IF NOT EXISTS `hy_wecom_webhook_action` (
    `id` INT NOT NULL AUTO_INCREMENT COMMENT '配置ID',
    `action_key` VARCHAR(64) NOT NULL COMMENT '系统通知动作标识',
    `enabled` TINYINT DEFAULT 0 COMMENT '是否启用:0否 1是',
    `msgtype` VARCHAR(32) DEFAULT 'text' COMMENT '企业微信消息类型',
    `webhook_url` VARCHAR(512) DEFAULT '' COMMENT '动作级Webhook地址，留空继承全局',
    `extra_template` JSON COMMENT '图文、卡片或媒体扩展配置JSON',
    `create_time` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_wecom_action_key` (`action_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业微信通知动作配置表';

INSERT IGNORE INTO `hy_wecom_webhook_action`
    (`action_key`, `enabled`, `msgtype`, `extra_template`, `create_time`, `update_time`)
SELECT `action_key`, `enabled`, `msgtype`, `extra_template`, `create_time`, `update_time`
FROM `hy_wecom_webhook_rule`;
