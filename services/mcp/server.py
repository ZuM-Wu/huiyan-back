"""
MCP 服务端实例（模块级单例）

- mcp: FastMCP 实例，鉴权用个人 API Key（ApiKeyVerifier），
       权限过滤中间件在实例创建后立即注册
- build_http_app(): 构建 Streamable HTTP 子应用，由 main.py 挂载到
       settings.MCP_MOUNT_PATH，其 lifespan 经 combine_lifespans 合并进主应用
       （历史坑: 不合并双 lifespan 会导致 MCP session manager 未初始化、请求挂死）
- stateless_http=True: 无状态模式，适配单进程部署与工具动态增删
"""
try:
    # FastMCP 3.4.x 在导入服务类型时需要 mcp.types 已完成初始化。
    import importlib
    mcp_module = importlib.import_module("mcp")
    mcp_types = importlib.import_module("mcp.types")
    setattr(mcp_module, "types", mcp_types)
    # FastMCP 3.x 将服务类从顶层导出移至 fastmcp.server。
    from fastmcp.server import FastMCP
except ImportError:  # 兼容旧版 FastMCP
    from fastmcp import FastMCP

from services.mcp.auth import ApiKeyVerifier
from services.mcp.middleware import PermissionFilterMiddleware


class McpPathMiddleware:
    """内部归一化 MCP 尾斜杠，保留原 Authorization，避免客户端重定向丢头。"""

    def __init__(self, app, mount_path: str):
        self.app = app
        self.mount_path = mount_path.rstrip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == self.mount_path:
            scope = dict(scope)
            scope["path"] = self.mount_path + "/"
            scope["raw_path"] = scope["path"].encode("utf-8")
        await self.app(scope, receive, send)

# FastMCP 单例：所有工具注册/注销均作用于该实例
mcp = FastMCP(
    "慧眼护农MCP",
    version="3.4.12",
    instructions="系统 MCP 能力版本 3.4.12：农业、硬件及启用插件。全局精简：最多64工具，8000估算Tokens，描述120字符，单次工具结果1024字节。truncated表示省略；确认工具须用户确认后传confirmed=true。",
    auth=ApiKeyVerifier(),
)
mcp.add_middleware(PermissionFilterMiddleware())


def build_http_app():
    """构建可挂载进 FastAPI 的 Streamable HTTP 子应用

    path="/": 子应用内部路径设为根，否则挂载后真实端点会变成
    {MCP_MOUNT_PATH}/mcp（FastMCP 默认内部 path 为 /mcp 的叠加坑）
    """
    return mcp.http_app(path="/", stateless_http=True)
