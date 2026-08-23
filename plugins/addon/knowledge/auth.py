# -*- coding: utf-8 -*-
"""
农业知识库插件权限定义

权限树在插件安装时由 PluginManager 调用 Plugin.get_permissions() 读取，
经 core.auth.rbac.register_plugin_permissions() 写入 hy_permission 表。

结构约定：
- 顶级节点：功能分组，code 以 :* 结尾，url 指向插件主页面（用于目录树展示）
- 子节点：细粒度操作权限，code 与 router 中 require_permission(code) 一一对应
"""

# 插件权限树（供 Plugin.get_permissions() 返回）
permission_tree = [
    {
        "title": "农业知识库",
        "code": "knowledge:*",
        "url": "/admin/plugin/knowledge/knowledge",
        "children": [
            {"title": "查看", "code": "knowledge:list"},
            {"title": "新增条目", "code": "knowledge:create"},
            {"title": "编辑条目", "code": "knowledge:update"},
            {"title": "删除条目", "code": "knowledge:delete"},
            {"title": "分类管理", "code": "knowledge:category"},
            {"title": "勘误审核", "code": "knowledge:correction"},
        ],
    }
]
