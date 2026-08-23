-- 文件下载插件删表脚本
-- 由 plugin.uninstall() 读取并执行
DROP TABLE IF EXISTS hy_plugin_file_download_area;
DROP TABLE IF EXISTS hy_plugin_file_download_file;
DROP TABLE IF EXISTS hy_plugin_file_download_folder
