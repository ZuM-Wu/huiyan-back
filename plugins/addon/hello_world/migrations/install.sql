-- Hello World 插件建表脚本
-- 由 plugin.install() 读取并执行；使用 IF NOT EXISTS 保证可重复安装
CREATE TABLE IF NOT EXISTS hy_plugin_hello_world_message (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '留言ID',
    title VARCHAR(128) NOT NULL COMMENT '留言标题',
    content TEXT NOT NULL COMMENT '留言内容',
    author VARCHAR(64) DEFAULT '' COMMENT '留言人',
    status INT DEFAULT 1 COMMENT '状态：0=隐藏, 1=显示',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hello World 插件留言表';
