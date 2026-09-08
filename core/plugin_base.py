"""插件抽象基类，独立于插件发现和升级流程。"""

from abc import ABC, abstractmethod
from typing import List, Optional

from core.events.models import EventDefinition, EventSubscription
from core.events.pipeline import PipelineHandler
from services.task.definitions import TaskDefinition


class BasePlugin(ABC):
    """所有业务插件共享的生命周期与能力声明。"""

    name: str = ""
    title: str = ""
    version: str = "1.0.0"
    description: str = ""
    module: str = "addon"

    def __init__(self, db_session=None, config: Optional[dict] = None):
        self.db = db_session
        self.config = config or {}

    @abstractmethod
    async def install(self) -> bool:
        """完成插件安装、表结构、钩子、权限和默认配置初始化。"""
        ...

    @abstractmethod
    async def uninstall(self) -> bool:
        """完成插件卸载及相关运行态、权限和配置清理。"""
        ...

    async def can_uninstall(self) -> bool:
        """在产生任何卸载副作用前确认插件是否允许卸载。"""
        return True

    def get_event_subscriptions(self) -> List[EventSubscription]:
        return []

    def get_event_definitions(self) -> List[EventDefinition]:
        return []

    def get_pipeline_handlers(self) -> List[PipelineHandler]:
        return []

    def get_task_definitions(self) -> List[TaskDefinition]:
        return []

    def get_routers(self) -> List:
        return []

    def get_permissions(self) -> List[dict]:
        return []

    def get_pages(self) -> List[dict]:
        return []

    def upgrade(self, old_version: str) -> bool:
        return True

    def get_config_schema(self) -> List[dict]:
        return []

    def get_hardware_providers(self) -> list:
        """声明本插件的硬件来源与可选详情组件，默认无硬件能力。"""
        return []

    def get_mcp_tools(self) -> List[dict]:
        return []

    def get_upload_policy_schema(self) -> List[dict]:
        return []

    async def on_runtime_enable(self) -> None:
        return None

    async def on_runtime_disable(self) -> None:
        return None

    async def _exec_sql(self, sql: str) -> None:
        """执行插件迁移 SQL，按分号拆分以保持现有数据库门面契约。"""
        if not self.db:
            return
        from sqlalchemy import text

        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                await self.db.execute(text(statement))
