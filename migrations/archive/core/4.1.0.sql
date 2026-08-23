-- 4.1.0 清理 MCP/API Key 未使用表
-- MCP 功能尚未集成，清理相关表定义，待集成时重建
-- 执行日期: 2026-07-21

DROP TABLE IF EXISTS `hy_api_key_permission`;
DROP TABLE IF EXISTS `hy_api_key`;
DROP TABLE IF EXISTS `hy_mcp_tool`;
