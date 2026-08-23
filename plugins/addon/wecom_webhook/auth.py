# -*- coding: utf-8 -*-
"""
企业微信通知插件 — 权限树定义

包含配置管理、消息发送、日志查看三类权限
"""
permission_tree = [{
    "title": "企业微信通知",
    "code": "wecom_webhook:*",
    "children": [
        {"title": "查看配置", "code": "wecom_webhook:config:view"},
        {"title": "更新配置", "code": "wecom_webhook:config:update"},
        {"title": "发送消息", "code": "wecom_webhook:message:send"},
        {"title": "查看日志", "code": "wecom_webhook:log:view"},
    ]
}]
