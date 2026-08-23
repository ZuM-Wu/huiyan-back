"""
core/ai — AI 大模型对话服务层

包结构:
- sse.py:        统一 SSE 事件信封构造与格式化（前端/App 兼容契约）
- driver.py:     LLM 驱动插件路由（module="llm"，复刻通知渠道 resolve_plugin 范式）
- service.py:    会话/消息管理与全局对话参数读取
- tool_bridge.py: MCP 工具桥（系统工具声明导出、权限过滤、进程内调用）
- agent_loop.py: 对话主循环（驱动流式输出 + 工具调用回合，产出统一 SSE 事件）

方向澄清: core/mcp/ 是「外部 AI 客户端调用本系统」，本包是「本系统调用外部
大模型」，二者互补 —— AI 对话的工具能力直接复用系统 MCP 工具注册表。
"""
