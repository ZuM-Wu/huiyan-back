# -*- coding: utf-8 -*-
"""资料下载插件上传策略声明。"""
from services.upload_policy import plugin_policy_definition

PLUGIN_INFO = {"name": "file_download", "title": "文件下载", "status": 1}

POLICIES = [{
    "key": "resource", "label": "资料下载", "description": "农户端可下载资料",
    "default_max_size_mb": 50,
    "default_extensions": [
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt",
        "csv", "zip", "rar", "7z", "jpg", "jpeg", "png", "mp4",
    ],
    "extensions_editable": True,
    "legacy_keys": {"max_size_mb": "max_size", "extensions": "extensions"},
}]


def get_policy_definition() -> dict:
    """返回供插件运行时读取的完整策略定义。"""
    return plugin_policy_definition(PLUGIN_INFO, POLICIES[0])
