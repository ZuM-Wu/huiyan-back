# -*- coding: utf-8 -*-
"""App 管理插件上传策略声明。"""
from services.upload_policy import plugin_policy_definition

PLUGIN_INFO = {"name": "app_manage", "title": "App管理", "status": 1}

POLICIES = [
    {
        "key": "apk", "label": "App APK", "description": "农户端安装包",
        "default_max_size_mb": 300, "default_extensions": ["apk"],
        "extensions_editable": False,
        "legacy_keys": {"max_size_mb": "apk_max_size"},
    },
    {
        "key": "ad_image", "label": "App 广告图",
        "description": "农户端开屏广告素材",
        "default_max_size_mb": 5,
        "default_extensions": ["jpg", "jpeg", "png", "webp"],
        "extensions_editable": True,
        "allowed_extensions": ["jpg", "jpeg", "png", "webp"],
        "legacy_keys": {"max_size_mb": "ad_img_max_size"},
    },
]


def get_policy_definition(key: str) -> dict:
    """返回供插件运行时读取的完整策略定义。"""
    item = next(item for item in POLICIES if item["key"] == key)
    return plugin_policy_definition(PLUGIN_INFO, item)
