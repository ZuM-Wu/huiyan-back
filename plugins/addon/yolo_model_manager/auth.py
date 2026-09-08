# -*- coding: utf-8 -*-
"""智能识别插件权限树。"""

permission_tree = [
    {
        "title": "智能识别",
        "code": "yolo_model_manager:*",
        "url": "/admin/plugin/yolo_model_manager/yolo_model_manager",
        "children": [
            {"title": "查看模型", "code": "yolo_model_manager:list"},
            {"title": "上传模型", "code": "yolo_model_manager:create"},
            {"title": "编辑模型", "code": "yolo_model_manager:update"},
            {"title": "下载模型", "code": "yolo_model_manager:download"},
            {"title": "绑定地块", "code": "yolo_model_manager:bind"},
            {"title": "删除模型", "code": "yolo_model_manager:delete"},
            {"title": "写入识别记录", "code": "yolo_model_manager:record:create"},
            {"title": "执行快捷检测", "code": "yolo_model_manager:detect"},
            {"title": "识别联合分析", "code": "yolo_model_manager:analysis"},
        ],
    }
]
