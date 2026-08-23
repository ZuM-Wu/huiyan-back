-- 企业微信通知插件 2.0.0 -> 2.1.0 独立 Markdown 正文模板迁移
ALTER TABLE `hy_wecom_webhook_action`
    ADD COLUMN `content_template` TEXT COMMENT 'Markdown/Markdown V2 正文模板'
    AFTER `webhook_url`;

-- 保留旧版规则表中的企业微信正文配置，统一动作表已有配置不覆盖。
UPDATE `hy_wecom_webhook_action` AS action
INNER JOIN `hy_wecom_webhook_rule` AS rule
    ON action.`action_key` = rule.`action_key`
SET action.`content_template` = rule.`content_template`
WHERE (action.`content_template` IS NULL OR action.`content_template` = '')
  AND rule.`content_template` IS NOT NULL
  AND rule.`content_template` <> '';
