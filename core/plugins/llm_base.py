# -*- coding: utf-8 -*-
"""
大模型（LLM）驱动插件抽象基类
定义 LLM 平台插件（DeepSeek/通义/Kimi 等）的统一接口规范

- LlmBasePlugin: LLM 驱动插件基类（llm_chat_stream / llm_list_models 方法）

设计对标 core/plugins/weather_base.py 中的 WeatherPluginBase：
插件仅作为"模型调用渠道"，不注册自身页面，由 core/ai/ 服务层
统一调度，配置通过 hy_configuration 表以 "{插件名}.{键}" 前缀存储。

llm_chat_stream 产出统一事件块约定（归一化在插件内完成，核心模块不感知平台差异）:
    {"type": "content_delta",   "data": {"text": "增量正文文本"}}
    {"type": "reasoning_delta", "data": {"text": "增量思维链文本（仅推理模型）"}}
    {"type": "tool_call",       "data": {"tool_calls": [  # 模型请求调用工具（一轮聚合后产出）
        {"id": "调用ID", "name": "工具名", "arguments": "JSON字符串参数"},
    ]}}
    {"type": "done",  "data": {"finish_reason": "stop/tool_calls/length",
                               "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}}}
    {"type": "error", "data": {"code": "错误码", "message": "中文错误描述"}}

约定说明:
    - 事件类型与 core/ai/sse.py 的 SSE 信封同名子集，服务层可直接透传；
    - tool_call 事件在流内 tool_calls 增量拼装完成后一次性产出（done 之前）；
    - 驱动内部必须捕获平台异常并归一化为 error 事件，不向上抛裸异常。
"""
import logging
from abc import abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)


class LlmBasePlugin(BasePlugin):
    """
    LLM 驱动插件抽象基类

    子类必须实现 llm_chat_stream() 与 llm_list_models()，其余方法按需覆写。

    llm_chat_stream 入参约定（OpenAI 风格，跨平台通用）:
        messages: [{"role": "system/user/assistant/tool", "content": "...",
                    "tool_calls": [...]（assistant 请求工具时）,
                    "tool_call_id": "..."（tool 结果回填时）}]
        tools:    [{"type": "function", "function": {"name", "description", "parameters"}}]
                  为空列表时表示本轮不暴露任何工具
        options:  {"model": 模型名, "temperature": 可选, "max_tokens": 可选,
                    "thinking": 可选 {"type": "enabled/disabled"},
                    "reasoning_effort": 可选 "low/high/max"}
    """

    module = "llm"
    # 驱动是否允许 Agent 将当前轮图片 Data URL 直接注入模型请求。
    supports_vision_input = False

    async def install(self) -> bool:
        """默认安装：写入 plugin.json 中声明的默认配置（已存在的键不覆盖）"""
        if not self.db:
            return True
        from core.config_manager import ConfigManager
        cm = ConfigManager()
        for key, value in (self.config or {}).items():
            existing = await cm.get(key, self.db)
            if existing is None:
                await cm.set(key, str(value), self.db, description=f"{self.name} LLM插件配置")
        logger.info(f"[{self.name}] LLM 插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """默认卸载：清理以插件名为前缀的配置"""
        if not self.db:
            return True
        from sqlalchemy import delete
        from core.db.configuration import ConfigurationModel
        await self.db.execute(
            delete(ConfigurationModel).where(
                ConfigurationModel.key.like(f"{self.name}.%")
            )
        )
        logger.info(f"[{self.name}] LLM 插件卸载完成")
        return True

    # ------------------------------------------------------------------
    # 模型调用接口
    # ------------------------------------------------------------------
    @abstractmethod
    async def llm_chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式对话补全，产出统一事件块（见模块 docstring）

        参数:
            messages: OpenAI 风格消息列表（含 system/user/assistant/tool）
            tools:    OpenAI function 工具声明列表（空列表=不启用工具）
            options:  调用选项 {"model": 模型名, "temperature"?, "max_tokens"?}
        产出:
            统一事件块字典；平台异常须归一化为 {"type": "error", ...} 事件后结束
        """
        ...

    @abstractmethod
    async def llm_list_models(self) -> List[Dict[str, Any]]:
        """
        列出本驱动支持的模型

        返回:
            [{"name": 模型标识, "label": 展示名,
              "support_tools": 是否支持工具调用,
              "support_reasoning": 是否输出思维链,
              "support_vision": 是否支持图片输入（多模态）}]
        """
        ...

    async def llm_get_model_capabilities(self, model_name: str) -> Dict[str, Any]:
        """返回指定模型能力；未知模型按保守的纯文本模型处理。"""
        models = await self.llm_list_models()
        for model in models:
            if str(model.get("name") or "").lower() == str(model_name or "").lower():
                return dict(model)
        return {
            "name": str(model_name or ""),
            "label": str(model_name or ""),
            "category": "unknown",
            "support_tools": False,
            "support_reasoning": False,
            "support_vision": False,
            "deprecated": False,
        }

    async def test_connection(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """测试插件连通性（配置有效性校验，可选覆写为真实探活）"""
        return {"success": True, "message": "插件已就绪（未实现真实连接测试）"}
