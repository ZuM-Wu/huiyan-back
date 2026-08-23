"""慧眼核心业务事件目录。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.events.models import EventDefinition
from core.events.registry import event_registry


class StrictPayload(BaseModel):
    """事件载荷拒绝未登记字段，防止敏感信息意外进入 Outbox。"""

    model_config = ConfigDict(extra="forbid")


class LoginPayload(StrictPayload):
    user_id: int
    ip: str = ""


class PluginPayload(StrictPayload):
    plugin_name: str


class ConfigPayload(StrictPayload):
    key: str
    value: Any = None


class FarmerRegisteredPayload(StrictPayload):
    farmer_id: int
    username: str
    phone: str = ""
    email: str = ""
    nickname: str = ""


class CertificationReviewedPayload(StrictPayload):
    farmer_id: int
    status: int
    real_name: str = ""
    review_remark: str = ""


class AreaFarmerBoundPayload(StrictPayload):
    area_id: int
    area_name: str
    farmer_ids: list[int] = Field(default_factory=list)
    action: str = "bind"


class PlotCreatedPayload(StrictPayload):
    plot_id: int
    area_id: int
    plot_name: str


class BatchStatusChangedPayload(StrictPayload):
    batch_id: int
    farmer_id: int
    old_status: str
    new_status: str
    batch_name: str


class WeatherAlertPayload(StrictPayload):
    area_id: int
    area_name: str
    alert_fields: dict[str, Any]


class TaskFailedPayload(StrictPayload):
    task_id: int
    owner: str
    definition: str
    attempt: int
    max_attempts: int
    error_msg: str
    correlation_id: str = ""
    event_id: int | None = None


class NoticeSendingPayload(StrictPayload):
    action_key: str
    recipient: str
    channel: str
    content: str
    variables: dict[str, Any] = Field(default_factory=dict)


class PlatformLifecyclePayload(StrictPayload):
    """主题/插件更新生命周期载荷，禁止写入包内容和凭据。"""

    plugin_name: str = ""
    surface: str = ""
    theme_id: str = ""
    version: str = ""
    operation_id: str = ""


_CORE_EVENTS = (
    EventDefinition("system.startup", "系统启动", "系统", 1, "system", StrictPayload, "transient"),
    EventDefinition("system.shutdown", "系统关闭", "系统", 1, "system", StrictPayload, "transient"),
    EventDefinition("admin.login", "管理员登录", "账户", 1, "system", LoginPayload, "transient", export_fields=frozenset({"user_id", "ip"})),
    EventDefinition("farmer.login", "农户登录", "账户", 1, "system", LoginPayload, "transient", export_fields=frozenset({"user_id", "ip"})),
    EventDefinition("plugin.installed", "插件安装", "插件", 1, "system", PluginPayload, "transient"),
    EventDefinition("plugin.uninstalled", "插件卸载", "插件", 1, "system", PluginPayload, "transient"),
    EventDefinition("config.changed", "系统配置变更", "系统", 1, "system", ConfigPayload, "durable", True, sensitive_fields=frozenset({"value"}), export_fields=frozenset({"key"})),
    EventDefinition("farmer.registered", "农户注册", "账户", 1, "system", FarmerRegisteredPayload, "durable", True, frozenset({"phone", "email"}), frozenset({"farmer_id", "username", "nickname"})),
    EventDefinition("certification.reviewed", "实名认证审核", "认证", 1, "system", CertificationReviewedPayload, "durable", True, frozenset({"real_name"}), frozenset({"farmer_id", "status", "review_remark"})),
    EventDefinition("area.farmer_bound", "产区绑定农户", "生产", 1, "system", AreaFarmerBoundPayload, "durable", True, export_fields=frozenset({"area_id", "area_name", "farmer_ids", "action"})),
    EventDefinition("plot.created", "地块创建", "生产", 1, "system", PlotCreatedPayload, "durable", True, export_fields=frozenset({"plot_id", "area_id", "plot_name"})),
    EventDefinition("batch.status_changed", "种植批次状态变更", "生产", 1, "system", BatchStatusChangedPayload, "durable", True, export_fields=frozenset({"batch_id", "farmer_id", "old_status", "new_status", "batch_name"})),
    EventDefinition("weather.alert", "气象预警", "气象", 1, "system", WeatherAlertPayload, "durable", True, export_fields=frozenset({"area_id", "area_name", "alert_fields"})),
    EventDefinition("task.failed", "任务最终失败", "任务", 1, "system", TaskFailedPayload, "durable", True, export_fields=frozenset({"task_id", "owner", "definition", "attempt", "max_attempts", "error_msg", "correlation_id", "event_id"})),
    EventDefinition("notice.sending", "通知即将发送", "通知", 1, "system", NoticeSendingPayload, "transient", export_fields=frozenset({"action_key", "channel", "content", "variables"})),
    EventDefinition("plugin.update.prepared", "插件更新预检完成", "插件", 1, "system", PlatformLifecyclePayload, "durable", True, export_fields=frozenset({"plugin_name", "version", "operation_id"})),
    EventDefinition("plugin.update.confirmed", "插件更新已确认", "插件", 1, "system", PlatformLifecyclePayload, "durable", True, export_fields=frozenset({"plugin_name", "version", "operation_id"})),
    EventDefinition("plugin.update.failed", "插件更新失败", "插件", 1, "system", PlatformLifecyclePayload, "durable", True, export_fields=frozenset({"plugin_name", "operation_id"})),
    EventDefinition("plugin.enabled", "插件已启用", "插件", 1, "system", PlatformLifecyclePayload, "transient", export_fields=frozenset({"plugin_name"})),
    EventDefinition("plugin.disabled", "插件已禁用", "插件", 1, "system", PlatformLifecyclePayload, "transient", export_fields=frozenset({"plugin_name"})),
    EventDefinition("theme.activated", "主题已激活", "主题", 1, "system", PlatformLifecyclePayload, "durable", True, export_fields=frozenset({"surface", "theme_id", "version", "operation_id"})),
    EventDefinition("theme.rollback", "主题已回退", "主题", 1, "system", PlatformLifecyclePayload, "durable", True, export_fields=frozenset({"surface", "theme_id", "operation_id"})),
)


def register_core_events() -> None:
    """幂等登记核心事件定义。"""
    for definition in _CORE_EVENTS:
        event_registry.register_definition(definition)
