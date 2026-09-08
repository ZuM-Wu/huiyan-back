-- 先删除识别记录与绑定表，再删除模型表，避免残留插件私有数据。
DROP TABLE IF EXISTS hy_plugin_yolo_model_manager_recognition;
DROP TABLE IF EXISTS hy_plugin_yolo_model_manager_binding;
DROP TABLE IF EXISTS hy_plugin_yolo_model_manager_model;
