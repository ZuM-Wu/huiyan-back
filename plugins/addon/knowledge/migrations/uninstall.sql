-- 农业知识库插件删表脚本
-- 由 plugin.uninstall() 读取并执行（逆序 DROP）
DROP TABLE IF EXISTS hy_plugin_knowledge_correction;
DROP TABLE IF EXISTS hy_plugin_knowledge_entry;
DROP TABLE IF EXISTS hy_plugin_knowledge_category
