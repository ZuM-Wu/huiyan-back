# -*- coding: utf-8 -*-
"""产区管理演示插件权限定义。"""

permission_tree = [
    {
        "title": "产区管理演示",
        "code": "production_area_management:*",
        "url": "/admin/plugin/production_area_management/index",
        "children": [
            {"title": "查看演示数据", "code": "production_area_management:list"},
            {"title": "生成产区日志", "code": "production_area_management:log:generate"},
            {"title": "AI 生成任务", "code": "production_area_management:task:generate"},
            {"title": "提交任务反馈", "code": "production_area_management:feedback"},
            {"title": "恢复演示状态", "code": "production_area_management:reset"},
        ],
    }
]
