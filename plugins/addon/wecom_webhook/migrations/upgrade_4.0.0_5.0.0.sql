-- 5.0.0 企业微信通知统一改为模板卡片，删除动作 Markdown 模板。
ALTER TABLE `hy_wecom_webhook_action`
    DROP COLUMN `markdown_template`;

ALTER TABLE `hy_wecom_webhook_log`
    MODIFY COLUMN `msgtype` VARCHAR(32) DEFAULT 'template_card' COMMENT '消息类型';
