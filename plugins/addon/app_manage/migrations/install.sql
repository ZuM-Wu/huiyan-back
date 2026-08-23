-- App管理插件建表脚本
-- 由 plugin.install() 读取并执行；使用 IF NOT EXISTS 保证可重复安装

CREATE TABLE IF NOT EXISTS hy_plugin_app_manage_version (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '版本记录ID',
    version_name VARCHAR(32) NOT NULL COMMENT '版本名(如1.2.0)',
    version_code INT NOT NULL COMMENT '版本号(App端数值比较用)',
    build_number VARCHAR(32) NOT NULL DEFAULT '' COMMENT '内部构建号',
    apk_filename VARCHAR(64) NOT NULL COMMENT 'APK磁盘文件名(UUID重命名)',
    apk_size BIGINT NOT NULL DEFAULT 0 COMMENT 'APK文件大小(字节)',
    apk_md5 CHAR(32) NOT NULL DEFAULT '' COMMENT 'APK文件MD5(App端下载校验)',
    changelog TEXT COMMENT '更新日志',
    update_policy TINYINT NOT NULL DEFAULT 1 COMMENT '更新策略 0可忽略 1提示可稍后 2强制',
    status TINYINT(1) NOT NULL DEFAULT 1 COMMENT '状态 1发布 0下架',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '操作管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_version_code (version_code),
    INDEX idx_pub (status, version_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='App管理插件-App版本表';

CREATE TABLE IF NOT EXISTS hy_plugin_app_manage_ad (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '广告ID(业务上仅使用id=1单行)',
    image_filename VARCHAR(64) NOT NULL DEFAULT '' COMMENT '广告图磁盘文件名(UUID重命名)',
    cache_key VARCHAR(64) NOT NULL DEFAULT '' COMMENT '素材缓存标识(App端比对判断是否重新下载)',
    link_url VARCHAR(500) NOT NULL DEFAULT '' COMMENT '点击跳转URL(空则不跳转)',
    start_time DATETIME DEFAULT NULL COMMENT '投放开始时间(NULL=立即)',
    end_time DATETIME DEFAULT NULL COMMENT '投放结束时间(NULL=不限)',
    duration TINYINT NOT NULL DEFAULT 3 COMMENT '开屏展示秒数',
    enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否启用 0禁用 1启用',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '最后操作管理员ID',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='App管理插件-开屏广告表';

CREATE TABLE IF NOT EXISTS hy_plugin_app_manage_notice (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '公告ID',
    title VARCHAR(200) NOT NULL COMMENT '公告标题',
    content TEXT COMMENT '公告内容',
    is_popup TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否弹窗提示 0否 1是',
    start_time DATETIME DEFAULT NULL COMMENT '生效开始时间(NULL=立即)',
    end_time DATETIME DEFAULT NULL COMMENT '生效结束时间(NULL=永久)',
    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用 0禁用 1启用',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '排序值(越小越靠前)',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '操作管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_active (enabled, start_time, end_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='App管理插件-App公告表';

-- 幂等种入开屏广告单行记录(id=1，默认禁用)，管理端仅更新该行
INSERT IGNORE INTO hy_plugin_app_manage_ad (id, enabled) VALUES (1, 0)
