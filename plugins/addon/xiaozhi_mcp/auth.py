# -*- coding: utf-8 -*-
"""小智 AI MCP 插件权限定义。"""

permission_tree = [
    {
        "title": "小智 AI MCP",
        "code": "xiaozhi_mcp:*",
        "url": "/admin/plugin/xiaozhi_mcp/xiaozhi_mcp",
        "children": [
            {"title": "查看小智 MCP", "code": "xiaozhi_mcp:list"},
            {"title": "管理小智 MCP", "code": "xiaozhi_mcp:manage"},
        ],
    }
]
