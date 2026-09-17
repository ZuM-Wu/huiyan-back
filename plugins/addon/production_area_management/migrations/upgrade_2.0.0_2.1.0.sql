-- 产区管理插件 2.0.0 -> 2.1.0
-- 为按日事实补充管理员自定义图片；幂等探测后再新增，保留全部现有业务数据。

SET @pam_ddl := IF((SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'hy_plugin_production_area_management_log'
          AND COLUMN_NAME = 'images_json') = 0,
    'ALTER TABLE hy_plugin_production_area_management_log ADD COLUMN images_json TEXT NULL COMMENT ''管理员自定义日报图片稳定地址JSON数组'' AFTER recommendation',
    'DO 0');
PREPARE pam_stmt FROM @pam_ddl;
EXECUTE pam_stmt;
DEALLOCATE PREPARE pam_stmt;
