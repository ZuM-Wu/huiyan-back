-- 文件下载插件建表脚本
-- 由 plugin.install() 读取并执行；使用 IF NOT EXISTS 保证可重复安装

CREATE TABLE IF NOT EXISTS hy_plugin_file_download_folder (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '文件夹ID',
    name VARCHAR(100) NOT NULL COMMENT '文件夹名称',
    is_default TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否默认文件夹 0否 1是',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '最后操作管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件下载插件-文件夹表';

CREATE TABLE IF NOT EXISTS hy_plugin_file_download_file (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '文件ID',
    folder_id INT NOT NULL DEFAULT 0 COMMENT '所属文件夹ID',
    name VARCHAR(200) NOT NULL COMMENT '显示名称',
    filename VARCHAR(200) NOT NULL COMMENT '磁盘文件名(UUID重命名)',
    origin_name VARCHAR(200) NOT NULL COMMENT '原始文件名(下载时还原)',
    filetype VARCHAR(50) NOT NULL DEFAULT '' COMMENT '文件扩展名',
    filesize INT NOT NULL DEFAULT 0 COMMENT '文件大小(字节)',
    visible_range VARCHAR(20) NOT NULL DEFAULT 'all' COMMENT '可见范围 all:所有农户 area:指定产区绑定农户',
    hidden TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否隐藏 0显示 1隐藏',
    download_count INT NOT NULL DEFAULT 0 COMMENT '下载次数',
    description VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '文件描述',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '上传管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_folder (folder_id),
    INDEX idx_visible (hidden, visible_range)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件下载插件-文件表';

CREATE TABLE IF NOT EXISTS hy_plugin_file_download_area (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '关联ID',
    file_id INT NOT NULL DEFAULT 0 COMMENT '文件ID',
    area_id INT NOT NULL DEFAULT 0 COMMENT '产区ID',
    UNIQUE KEY uk_file_area (file_id, area_id),
    INDEX idx_area (area_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件下载插件-文件产区关联表';

INSERT INTO hy_plugin_file_download_folder (name, is_default)
SELECT '默认文件夹', 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_file_download_folder WHERE is_default = 1)
