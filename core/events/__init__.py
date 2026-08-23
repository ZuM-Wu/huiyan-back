"""事件平台公开门面。"""

from core.events.bus import event_bus
from core.events.definitions import register_core_events
from core.events.models import EventDefinition, EventSubscription
from core.events.pipeline import PipelineDecision, PipelineHandler, pipeline_engine
from core.events.registry import event_registry

# 核心定义属于事件公共门面的组成部分。导入门面即可使用事件，避免依赖
# Uvicorn lifespan 的调用顺序，也让独立脚本和单元测试获得相同契约。
register_core_events()

__all__ = [
    "EventDefinition",
    "EventSubscription",
    "PipelineDecision",
    "PipelineHandler",
    "event_bus",
    "event_registry",
    "pipeline_engine",
    "register_core_events",
]
