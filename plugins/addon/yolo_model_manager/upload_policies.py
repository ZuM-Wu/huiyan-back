# -*- coding: utf-8 -*-
"""智能识别模型文件上传策略声明。"""

from services.upload_policy import plugin_policy_definition

PLUGIN_INFO = {"name": "yolo_model_manager", "title": "智能识别", "status": 1}

POLICIES = [
    {
        "key": "model",
        "label": "YOLO模型文件",
        "description": "用于地块目标检测的 YOLO 模型权重或 ONNX 模型",
        "default_max_size_mb": 500,
        "default_extensions": ["pt", "onnx"],
        "extensions_editable": True,
    }
]


def get_policy_definition() -> dict:
    """返回插件运行时使用的完整模型上传策略。"""
    return plugin_policy_definition(PLUGIN_INFO, POLICIES[0])
