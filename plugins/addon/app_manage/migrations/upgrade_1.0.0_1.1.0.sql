-- App管理插件 1.0.0 -> 1.1.0 升级脚本
-- 变更：version 表新增 build_number 内部构建号；force_update(0/1) 升级为三态 update_policy
-- 注意：MySQL DDL 隐式提交，本脚本按语句顺序独立执行，旧值映射在 DROP 之前完成

ALTER TABLE hy_plugin_app_manage_version
    ADD COLUMN build_number VARCHAR(32) NOT NULL DEFAULT '' COMMENT '内部构建号' AFTER version_code;

ALTER TABLE hy_plugin_app_manage_version
    ADD COLUMN update_policy TINYINT NOT NULL DEFAULT 1 COMMENT '更新策略 0可忽略 1提示可稍后 2强制' AFTER changelog;

-- 旧语义映射：force_update=1(强制) -> 2；force_update=0(非强制) -> 1(提示可稍后)
UPDATE hy_plugin_app_manage_version SET update_policy = IF(force_update = 1, 2, 1);

ALTER TABLE hy_plugin_app_manage_version DROP COLUMN force_update
