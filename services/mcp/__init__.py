"""
MCP 服务包（FastMCP 单进程接入）

模块分工:
- server.py     FastMCP 实例与 HTTP 子应用构建（挂载到 FastAPI 主应用）
- auth.py       个人 API Key 鉴权（Bearer Token -> 用户身份 + 权限 scopes）
- middleware.py 工具权限过滤中间件（tools/list 按权限过滤，tools/call 越权拒绝）
- registry.py   工具注册表（核心/插件工具的动态注册与注销）
- tool_utils.py 工具共享助手（身份 claims 读取、农户产区绑定校验、
                管理员归因反查、Pydantic 校验入口）
- tools_core.py 系统核心工具聚合入口（天气快照、农户产区列表 + 聚合 CORE_TOOLS）
- tools_area.py    产区管理工具（产区/地块/批次三级树）
- tools_weather.py 天气增强工具（批次积温、逐日历史、气象预警）
- tools_admin.py   后台运维工具（农户列表、待处理失败任务查询/重试/标记闭环、
                   系统概览聚合计数）
- tools_farmer_ops.py  农户管理操作（启用/禁用农户、产区增量绑/解单农户）
- tools_area_write.py  产区三级写操作（产区/地块/批次创建与更新，不含删除）
- tools_weather_ops.py 天气运维（单产区强刷；全量拉取与数据源状态已下线）
- tools_notice.py      通知操作（给指定农户发站内信）

注意: 包名 core.mcp 与第三方顶层库 mcp 不冲突（Python3 绝对导入）。
"""
