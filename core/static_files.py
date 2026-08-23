"""带缓存策略的静态资源处理器（从 view_controller.py 提取，消除循环依赖）

view_controller 与 theme_manager 均需使用 CachedStaticFiles，
提取到独立模块后双方各自单向依赖本模块，不再互相依赖。
"""
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

# 第三方库长缓存时长（秒）— 一年，配合 immutable 表示内容永不变化
VENDOR_MAX_AGE = 31536000


class CachedStaticFiles(StaticFiles):
    """
    带缓存策略的静态资源处理器

    - /static/vendor/ 下的第三方库（vue/tdesign/axios）为不可变文件，
      下发 `Cache-Control: public, max-age=1年, immutable`，浏览器刷新时
      直接命中缓存、不再发起网络请求，彻底消除白屏等待。
    - 其余应用代码（页面 JS/CSS）沿用默认 ETag/Last-Modified 协商缓存，
      内容变更后可即时刷新，避免开发期缓存陈旧。
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        # path 为相对 static 目录的路径，形如 "vendor/vue.global.prod.js"
        normalized = path.replace("\\", "/")
        if normalized.startswith("vendor/"):
            response.headers["Cache-Control"] = f"public, max-age={VENDOR_MAX_AGE}, immutable"
        else:
            # 应用自有资源（页面 JS/CSS）：强制浏览器每次携带 ETag 协商，
            # 命中则 304 不传正文、变更则立即拉取新内容，杜绝启发式缓存导致的陈旧样式
            response.headers["Cache-Control"] = "no-cache"
        return response
