# -*- coding: utf-8 -*-
"""
文件下载插件权限定义

权限树在插件安装时由 PluginManager 调用 Plugin.get_permissions() 读取，
经 core.auth.rbac.register_plugin_permissions() 写入 hy_permission 表。

结构约定：
- 顶级节点：功能分组，code 以 :* 结尾，url 指向插件主页面（用于目录树展示）
- 子节点：细粒度操作权限，code 与 router 中 require_permission(code) 一一对应
"""

# 插件权限树（供 Plugin.get_permissions() 返回）
permission_tree = [
    {
        "title": "文件下载",
        "code": "file_download:*",
        "url": "/admin/plugin/file_download/file_download",
        "children": [
            {"title": "查看与下载", "code": "file_download:list"},
            {"title": "上传文件", "code": "file_download:create"},
            {"title": "编辑文件", "code": "file_download:update"},
            {"title": "删除文件", "code": "file_download:delete"},
            {"title": "文件夹管理", "code": "file_download:folder"},
        ],
    }
]
