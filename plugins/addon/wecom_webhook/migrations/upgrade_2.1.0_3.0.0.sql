-- 企业微信通知插件 2.1.0 -> 3.0.0 管理员动作独立化迁移
ALTER TABLE `hy_wecom_webhook_action`
    ADD COLUMN `action_name` VARCHAR(128) DEFAULT '' COMMENT '动作名称' AFTER `action_key`,
    ADD COLUMN `action_type` VARCHAR(32) DEFAULT 'other' COMMENT '动作分类' AFTER `action_name`,
    ADD COLUMN `markdown_template` TEXT COMMENT '管理员通知Markdown模板' AFTER `webhook_url`;

UPDATE `hy_wecom_webhook_action`
SET `markdown_template` = `content_template`
WHERE `content_template` IS NOT NULL AND `content_template` <> '';

ALTER TABLE `hy_wecom_webhook_action`
    DROP COLUMN `msgtype`,
    DROP COLUMN `content_template`,
    DROP COLUMN `extra_template`;

ALTER TABLE `hy_wecom_webhook_action`
    COMMENT = '企业微信管理员通知动作配置表';

DROP TABLE IF EXISTS `hy_wecom_webhook_rule`;
