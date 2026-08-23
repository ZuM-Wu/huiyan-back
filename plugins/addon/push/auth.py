# -*- coding: utf-8 -*-
"""
手动推送插件权限定义

权限树在插件安装时由 PluginManager 调用 Plugin.get_permissions() 读取，
经 core.auth.rbac.register_plugin_permissions() 写入 hy_permission 表。
"""

# 插件权限树（供 Plugin.get_permissions() 返回）
permission_tree = [
    {
        "title": "推送中心",
        "code": "push:*",
        "url": "/admin/plugin/push/notice-push",
        "children": [
            {"title": "查看推送任务", "code": "push:list"},
            {"title": "创建推送任务", "code": "push:create"},
            {"title": "编辑推送任务", "code": "push:update"},
            {"title": "删除推送任务", "code": "push:delete"},
            {"title": "发送推送", "code": "push:send"},
        ],
    }
]
