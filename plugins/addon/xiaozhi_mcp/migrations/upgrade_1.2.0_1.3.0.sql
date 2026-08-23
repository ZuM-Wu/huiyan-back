-- 小智 AI MCP 插件 1.2.0 -> 1.3.0 升级脚本
-- 创建插件专属表并一次性复制系统管理员 MCP 存量，保留原表不变。

CREATE TABLE IF NOT EXISTS hy_plugin_xiaozhi_mcp_server (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    name VARCHAR(64) NOT NULL COMMENT '服务器名称',
    url VARCHAR(256) NOT NULL COMMENT 'Streamable HTTP接入地址',
    api_key VARCHAR(256) NOT NULL DEFAULT '' COMMENT 'Bearer鉴权密钥（可为空）',
    config_json TEXT NULL COMMENT '完整JSON配置（Claude Desktop风格）',
    tools_cache TEXT NULL COMMENT '工具列表缓存JSON',
    status INT NOT NULL DEFAULT 1 COMMENT '状态：1启用，2停用',
    last_test_time DATETIME NULL COMMENT '最近测试时间',
    last_test_status INT NOT NULL DEFAULT 0 COMMENT '最近测试状态：0未测试，1成功，2失败',
    last_error VARCHAR(512) NOT NULL DEFAULT '' COMMENT '最近测试错误信息',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='小智AI MCP插件外部服务器表';

INSERT IGNORE INTO hy_plugin_xiaozhi_mcp_server (
    id, name, url, api_key, config_json, tools_cache, status,
    last_test_time, last_test_status, last_error, create_time, update_time
)
SELECT
    id, name, url, api_key, config_json, tools_cache, status,
    last_test_time, last_test_status, last_error, create_time, update_time
FROM hy_ai_mcp_server
