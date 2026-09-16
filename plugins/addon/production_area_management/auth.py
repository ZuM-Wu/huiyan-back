# -*- coding: utf-8 -*-
"""产区管理插件权限定义。"""

permission_tree = [
    {
        "title": "产区管理",
        "code": "production_area_management:*",
        "url": "/admin/plugin/production_area_management/index",
        "children": [
            {"title": "查看产区事实", "code": "production_area_management:list"},
            {"title": "同步产区日志", "code": "production_area_management:log:generate"},
            {"title": "根据建议生成任务", "code": "production_area_management:task:generate"},
            {"title": "提交现场反馈", "code": "production_area_management:feedback"},
        ],
    }
]
