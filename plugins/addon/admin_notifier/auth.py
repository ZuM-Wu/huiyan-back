# -*- coding: utf-8 -*-
"""
邮件通知管理员插件 — 权限树定义

仅包含配置管理相关权限（日志查询、重试、处理等已移至核心权限）
"""
permission_tree = [{
    "title": "邮件通知管理员",
    "code": "admin_notifier:*",
    "children": [
        {"title": "查看配置", "code": "admin_notifier:config:view"},
        {"title": "更新配置", "code": "admin_notifier:config:update"},
        {"title": "发送测试", "code": "admin_notifier:test:send"},
    ]
}]
