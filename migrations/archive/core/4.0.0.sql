-- ============================================
-- 慧眼护农 V4 核心表初始建表 SQL
-- 版本: 4.0.0
-- 对标 ZJMF: plugin/plugin_hook/configuration/admin/client/auth/system_log
-- ============================================

-- 插件注册表（对标 ZJMF 的 idcsmart_plugin）
-- module 字段区分插件类型: addon/gateway/sms/mail/captcha/certification/oauth/oss/server/widget
CREATE TABLE IF NOT EXISTS `hy_plugin` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name` varchar(64) NOT NULL COMMENT '插件唯一标识(snake_case)',
  `title` varchar(128) NOT NULL COMMENT '插件显示名称',
  `version` varchar(32) NOT NULL DEFAULT '1.0.0' COMMENT '版本号',
  `module` varchar(32) NOT NULL DEFAULT 'addon' COMMENT '插件类型: addon/gateway/sms/mail/captcha/certification/oauth/oss/server/widget',
  `status` tinyint NOT NULL DEFAULT '1' COMMENT '状态: 0=已安装未启用, 1=已启用, 2=已禁用',
  `config` text COMMENT '插件配置(JSON格式)',
  `install_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '安装时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_name` (`name`),
  KEY `idx_module` (`module`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='插件注册表';

-- 插件钩子注册表（对标 ZJMF 的 idcsmart_plugin_hook）
CREATE TABLE IF NOT EXISTS `hy_plugin_hook` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name` varchar(128) NOT NULL COMMENT '钩子名称（事件名，如 pest_detected）',
  `plugin` varchar(64) NOT NULL COMMENT '所属插件标识',
  `description` varchar(256) DEFAULT '' COMMENT '钩子描述',
  `priority` int NOT NULL DEFAULT '100' COMMENT '优先级，数字越小越先执行',
  `status` tinyint NOT NULL DEFAULT '1' COMMENT '状态: 0=禁用, 1=启用',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_plugin` (`plugin`),
  KEY `idx_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='插件钩子注册表';

-- 配置管理表（对标 ZJMF 的 idcsmart_configuration）
CREATE TABLE IF NOT EXISTS `hy_configuration` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `key` varchar(128) NOT NULL COMMENT '配置键（如 site_name, oss_method）',
  `value` text NOT NULL COMMENT '配置值',
  `description` varchar(256) DEFAULT '' COMMENT '配置说明',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='配置管理表';

-- 管理员表（对标 ZJMF 的 idcsmart_admin）
CREATE TABLE IF NOT EXISTS `hy_admin` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '管理员ID',
  `username` varchar(64) NOT NULL COMMENT '用户名',
  `password` varchar(256) NOT NULL COMMENT '密码哈希(SHA256)',
  `nickname` varchar(64) DEFAULT '' COMMENT '昵称',
  `email` varchar(128) DEFAULT '' COMMENT '邮箱',
  `phone` varchar(32) DEFAULT '' COMMENT '手机号',
  `status` tinyint DEFAULT '1' COMMENT '状态: 0=禁用, 1=启用',
  `last_login_ip` varchar(50) DEFAULT '' COMMENT '最后登录IP',
  `last_action_time` datetime COMMENT '最后操作时间',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员表';

-- 管理员角色表（对标 ZJMF 的 idcsmart_admin_role）
CREATE TABLE IF NOT EXISTS `hy_admin_role` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '角色ID',
  `name` varchar(64) NOT NULL COMMENT '角色名称',
  `description` varchar(256) DEFAULT '' COMMENT '描述',
  `is_system` tinyint DEFAULT '0' COMMENT '是否系统内置: 0=否, 1=是',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员角色表';

-- 管理员角色关联表（对标 ZJMF 的 idcsmart_admin_role_link）
CREATE TABLE IF NOT EXISTS `hy_admin_role_link` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `admin_id` int unsigned NOT NULL COMMENT '管理员ID',
  `role_id` int unsigned NOT NULL COMMENT '角色ID',
  PRIMARY KEY (`id`),
  KEY `idx_admin` (`admin_id`),
  KEY `idx_role` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员角色关联表';

-- 农户表（对标 ZJMF 的 idcsmart_client）
CREATE TABLE IF NOT EXISTS `hy_farmer` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '农户ID',
  `username` varchar(64) NOT NULL COMMENT '用户名',
  `password` varchar(256) NOT NULL COMMENT '密码哈希(SHA256)',
  `nickname` varchar(64) DEFAULT '' COMMENT '昵称',
  `email` varchar(128) DEFAULT '' COMMENT '邮箱',
  `phone` varchar(32) DEFAULT '' COMMENT '手机号',
  `company` varchar(128) DEFAULT '' COMMENT '公司/农场名',
  `status` tinyint DEFAULT '1' COMMENT '状态: 0=禁用, 1=启用',
  `last_login_ip` varchar(50) DEFAULT '' COMMENT '最后登录IP',
  `last_action_time` datetime COMMENT '最后操作时间',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农户表';

-- 权限节点表（对标 ZJMF 的 idcsmart_auth）
CREATE TABLE IF NOT EXISTS `hy_permission` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '权限ID',
  `title` varchar(128) NOT NULL COMMENT '权限标题',
  `code` varchar(128) NOT NULL COMMENT '权限标识,如 farm:create',
  `url` varchar(256) DEFAULT '' COMMENT '前端页面路径',
  `parent_id` int unsigned DEFAULT '0' COMMENT '父权限ID, 0=顶级',
  `sort_order` int DEFAULT '0' COMMENT '排序',
  `plugin` varchar(64) DEFAULT '' COMMENT '所属插件标识，空字符串=系统核心权限',
  `description` varchar(256) DEFAULT '' COMMENT '描述',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code` (`code`),
  KEY `idx_plugin` (`plugin`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限节点表';

-- 系统日志表（对标 ZJMF 的 idcsmart_system_log）
CREATE TABLE IF NOT EXISTS `hy_system_log` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '日志ID',
  `type` varchar(50) DEFAULT '' COMMENT '操作类型',
  `rel_id` int DEFAULT '0' COMMENT '关联ID',
  `description` text COMMENT '描述',
  `user_type` varchar(20) DEFAULT 'admin' COMMENT '操作人类型: admin/farmer/system/cron',
  `user_id` int DEFAULT '0' COMMENT '操作人ID',
  `user_name` varchar(100) DEFAULT '' COMMENT '操作人名称',
  `ip` varchar(50) DEFAULT '' COMMENT 'IP地址',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_user` (`user_type`, `user_id`),
  KEY `idx_type` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统日志表';

-- 菜单表（对标 ZJMF 的 menu 导航体系，ZJMF 用 PHP 数组定义，这里持久化到数据库）
CREATE TABLE IF NOT EXISTS `hy_menu` (
  `id` int unsigned NOT NULL AUTO_INCREMENT COMMENT '菜单ID',
  `name` varchar(64) NOT NULL COMMENT '菜单标识',
  `title` varchar(128) NOT NULL COMMENT '菜单标题',
  `path` varchar(256) DEFAULT '' COMMENT '前端路由路径',
  `icon` varchar(64) DEFAULT '' COMMENT '图标名称',
  `parent_id` int unsigned DEFAULT '0' COMMENT '父菜单ID, 0=顶级',
  `sort_order` int DEFAULT '0' COMMENT '排序',
  `plugin` varchar(64) DEFAULT '' COMMENT '所属插件标识，空字符串=系统核心菜单',
  `visible` tinyint DEFAULT '1' COMMENT '是否可见: 0=隐藏, 1=显示',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_parent` (`parent_id`),
  KEY `idx_plugin` (`plugin`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='菜单表';

-- ============================================
-- 种子数据（对标 ZJMF 安装即用）
-- ============================================

-- 系统核心插件注册
INSERT IGNORE INTO `hy_plugin` (`name`, `title`, `version`, `module`, `status`) VALUES
('system', '系统核心', '4.0.0', 'addon', 1);

-- 超级管理员（密码: 123456）
INSERT IGNORE INTO `hy_admin` (`id`, `username`, `password`, `nickname`, `status`) VALUES
(1, 'admin', '$12$/3VXJFgyLYddUXVFf0/58OlZSx7v20sWUz5Yn.wa3GhtDtzLS3j3C', '超级管理员', 1);

-- 管理员角色
INSERT IGNORE INTO `hy_admin_role` (`id`, `name`, `description`, `is_system`) VALUES
(1, '超级管理员', '系统内置超级管理员，拥有所有权限', 1),
(2, '运营管理员', '日常运营管理角色', 1);

-- 管理员-角色绑定
INSERT IGNORE INTO `hy_admin_role_link` (`admin_id`, `role_id`) VALUES
(1, 1);

-- 系统默认配置（对标 ZJMF 预设配置）
INSERT IGNORE INTO `hy_configuration` (`key`, `value`, `description`) VALUES
('site_name', '慧眼护农', '系统名称'),
('system_version', '4.0.0', '系统版本'),
('lang_admin', 'zh-cn', '后台默认语言'),
('lang_farmer', 'zh-cn', '前台默认语言'),
('jwt_expire_admin', '7200', '管理员JWT过期时间(秒)'),
('jwt_expire_farmer', '86400', '农户JWT过期时间(秒)'),
('login_max_attempts', '5', '最大登录尝试次数'),
('upload_max_size', '10', '上传文件最大大小(MB)'),
('record_lock', 'false', '是否开启维护模式'),
('maintenance_message', '系统维护中，请稍后再试', '维护模式提示信息');

-- 系统核心菜单树
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
-- 一级菜单
(1, 'dashboard', '控制台', '/dashboard', 'dashboard', 0, 1, '', 1),
(2, 'system', '系统设置', '/system', 'setting', 0, 99, '', 1),
(3, 'plugin', '插件管理', '/plugin', 'app', 0, 3, '', 1),
(4, 'user', '用户管理', '/user', 'user', 0, 4, '', 1),
(5, 'farmer', '农户管理', '/farmer', 'usergroup', 0, 5, '', 1),
(6, 'permission', '权限管理', '/permission', 'lock-on', 0, 6, '', 1),
(7, 'log', '系统日志', '/log', 'file', 0, 7, '', 1);

-- 二级菜单 - 系统设置
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
(8, 'system_basic', '基本设置', '/system/basic', '', 2, 1, '', 1),
(9, 'system_cache', '缓存管理', '/system/cache', '', 2, 2, '', 1);

-- 二级菜单 - 插件管理
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
(10, 'plugin_list', '已安装插件', '/plugin/list', '', 3, 1, '', 1);

-- 二级菜单 - 用户管理
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
(11, 'user_admin', '管理员列表', '/user/admin', '', 4, 1, '', 1),
(12, 'user_role', '角色管理', '/user/role', '', 4, 2, '', 1);

-- 二级菜单 - 农户管理
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
(13, 'farmer_list', '农户列表', '/farmer/list', '', 5, 1, '', 1);

-- 二级菜单 - 权限管理
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
(14, 'permission_node', '权限节点', '/permission/node', '', 6, 1, '', 1);

-- 二级菜单 - 系统日志
INSERT IGNORE INTO `hy_menu` (`id`, `name`, `title`, `path`, `icon`, `parent_id`, `sort_order`, `plugin`, `visible`) VALUES
(16, 'log_operation', '操作日志', '/log/operation', '', 7, 1, '', 1);

-- 系统核心权限节点
INSERT IGNORE INTO `hy_permission` (`id`, `title`, `code`, `parent_id`, `sort_order`, `plugin`, `description`) VALUES
(1, '控制台', 'dashboard', 0, 1, '', '控制台权限'),
(2, '系统设置', 'system', 0, 2, '', '系统设置权限'),
(3, '基本设置', 'system:basic', 2, 1, '', '基本设置'),
(4, '缓存管理', 'system:cache', 2, 2, '', '缓存管理'),
(5, '插件管理', 'plugin', 0, 3, '', '插件管理权限'),
(6, '插件列表', 'plugin:list', 5, 1, '', '查看插件列表'),
(7, '插件安装', 'plugin:install', 5, 2, '', '安装插件'),
(8, '插件卸载', 'plugin:uninstall', 5, 3, '', '卸载插件'),
(9, '用户管理', 'user', 0, 4, '', '用户管理权限'),
(10, '管理员列表', 'user:admin', 9, 1, '', '管理员管理'),
(11, '角色管理', 'user:role', 9, 2, '', '角色管理'),
(12, '农户管理', 'farmer', 0, 5, '', '农户管理权限'),
(13, '农户列表', 'farmer:list', 12, 1, '', '查看农户列表'),
(14, '权限管理', 'permission', 0, 6, '', '权限管理权限'),
(15, '权限节点', 'permission:node', 14, 1, '', '权限节点管理'),
(17, '系统日志', 'log', 0, 7, '', '系统日志权限'),
(18, '操作日志', 'log:operation', 17, 1, '', '查看操作日志');
