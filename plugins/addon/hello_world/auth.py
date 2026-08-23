# -*- coding: utf-8 -*-
"""
Hello World 插件权限定义

权限树在插件安装时由 PluginManager 调用 Plugin.get_permissions() 读取，
经 core.auth.rbac.register_plugin_permissions() 写入 hy_permission 表。

结构约定：
- 顶级节点：功能分组，code 以 :* 结尾，url 指向插件主页面（用于目录树展示）
- 子节点：细粒度操作权限，code 与 router.py 中 require_permission(code) 一一对应
"""

# 插件权限树（供 Plugin.get_permissions() 返回）
permission_tree = [
    {
        "title": "Hello World 示例",
        "code": "hello_world:*",
        "url": "/admin/plugin/hello_world/hello_world",
        "children": [
            {"title": "查看留言", "code": "hello_world:list"},
            {"title": "新增留言", "code": "hello_world:create"},
            {"title": "编辑留言", "code": "hello_world:update"},
            {"title": "删除留言", "code": "hello_world:delete"},
            {"title": "状态切换", "code": "hello_world:status"},
            {"title": "配置管理", "code": "hello_world:config"},
        ],
    }
]
