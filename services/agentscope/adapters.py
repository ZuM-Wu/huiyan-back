# -*- coding: utf-8 -*-
"""AgentScope 与慧眼平台公共能力的适配类型。

AgentScope 2.0.7 的 Service lifespan 会创建自己的 BackgroundTaskManager
和 SchedulerManager。这里保留项目命名的适配类型，作为后续接入任务队列
公共门面的唯一扩展点；默认行为仍由 AgentScope Service 管理并通过 MySQL
MessageBus 传递事件。
"""
import asyncio
import logging
import subprocess
from weakref import WeakValueDictionary

from agentscope.app._manager import BackgroundTaskManager, SchedulerManager
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.tool import ExecResult, LocalBackend
from pydantic import PrivateAttr

from services.mcp.system_endpoint import normalize_system_mcp_url


logger = logging.getLogger(__name__)


class HuiyanMCPClient(MCPClient):
    """隔离单个外部 MCP 的发现失败，并确保日志不包含连接凭据。"""

    _owner_user_id: str = PrivateAttr(default="")

    async def list_tools(self):
        try:
            return await super().list_tools()
        except Exception as exc:
            logger.warning(
                "MCP 工具发现失败，已从本轮撤回: user_id=%r mcp_name=%r "
                "exception_type=%s",
                self._owner_user_id,
                self.name,
                type(exc).__name__,
            )
            return []


class HuiyanLocalBackend(LocalBackend):
    """兼容 Windows Uvicorn reload 的本地命令后端。"""

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        try:
            return await super().exec_shell(command, cwd=cwd, timeout=timeout)
        except NotImplementedError:
            return await asyncio.to_thread(
                self._run_without_asyncio_subprocess,
                command,
                cwd,
                timeout,
            )

    @staticmethod
    def _run_without_asyncio_subprocess(
        command: list[str],
        cwd: str | None,
        timeout: float | None,
    ) -> ExecResult:
        """在线程中执行无 Shell 参数列表，避免 Selector 子进程限制。"""
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                timeout=timeout,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            return ExecResult(127, b"", str(exc).encode("utf-8"))
        except subprocess.TimeoutExpired:
            return ExecResult(-1, b"", b"timed out")
        return ExecResult(completed.returncode, completed.stdout, completed.stderr)


class HuiyanWorkspaceManager(LocalWorkspaceManager):
    """按资源所有者的启用状态自动收敛每个会话的 MCP。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._mcp_sync_locks: WeakValueDictionary[
            tuple[str, str, str, str],
            asyncio.Lock,
        ] = WeakValueDictionary()

    def bind_storage(self, storage: StorageBase) -> None:
        """保存 AgentScope Storage，作为管理员 MCP 资源库的唯一事实源。"""
        super().bind_storage(storage)

    @staticmethod
    def _use_huiyan_backend(workspace) -> None:
        if not isinstance(workspace.get_backend(), HuiyanLocalBackend):
            workspace._backend = HuiyanLocalBackend()

    @staticmethod
    def _client_config(client: MCPClient) -> dict:
        """只比较可持久化配置，排除连接状态与工具缓存。"""
        return client.model_dump(mode="json")

    @staticmethod
    def _runtime_client(client: MCPClient, user_id: str) -> HuiyanMCPClient:
        config = client.mcp_config
        normalized_url = (
            normalize_system_mcp_url(client.name, config.url)
            if isinstance(config, HttpMCPConfig)
            else ""
        )
        if isinstance(config, HttpMCPConfig) and normalized_url != config.url:
            client = client.model_copy(
                update={
                    "mcp_config": config.model_copy(
                        update={"url": normalized_url},
                    ),
                },
            )
        runtime_client = HuiyanMCPClient.model_validate(
            client.model_dump(mode="json"),
        )
        runtime_client._owner_user_id = user_id
        return runtime_client

    def _mcp_sync_lock(
        self,
        key: tuple[str, str, str, str],
    ) -> asyncio.Lock:
        """复用仍在执行或等待的锁，并让空闲会话锁自动释放。"""
        lock = self._mcp_sync_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._mcp_sync_locks[key] = lock
        return lock

    async def _sync_workspace_mcps(
        self,
        workspace,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        """把会话声明收敛到资源所有者当前全部已启用 MCP。"""
        workspace_id = str(getattr(workspace, "workspace_id", ""))
        lock = self._mcp_sync_lock(
            (user_id, workspace_id, agent_id, session_id),
        )
        async with lock:
            storage = self._storage
            if storage is None:
                raise RuntimeError("AgentScope Workspace Storage 尚未绑定")
            records = await storage.list_mcps(user_id)
            desired = {
                row.client.name: self._runtime_client(row.client, user_id)
                for row in records
                if row.enabled
            }
            declared = list(workspace._declared_specs(agent_id, session_id))

            # 停用、删除、旧手动绑定及配置变化都先从当前会话撤下。
            for current in declared:
                target = desired.get(current.name)
                needs_replace = (
                    target is not None
                    and (
                        not isinstance(current, HuiyanMCPClient)
                        or self._client_config(current)
                        != self._client_config(target)
                    )
                )
                if target is not None and not needs_replace:
                    continue
                try:
                    await workspace.remove_mcp(
                        current.name,
                        agent_id=agent_id,
                        session_id=session_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "MCP 自动移除失败: user_id=%r mcp_name=%r "
                        "exception_type=%s",
                        user_id,
                        current.name,
                        type(exc).__name__,
                    )

            current_names = {
                item.name
                for item in workspace._declared_specs(agent_id, session_id)
            }
            for name, target in desired.items():
                if name in current_names:
                    continue
                try:
                    await workspace.add_mcp(
                        target,
                        agent_id=agent_id,
                        session_id=session_id,
                    )
                    current_names.add(name)
                except Exception as exc:
                    logger.warning(
                        "MCP 自动装配失败: user_id=%r mcp_name=%r "
                        "exception_type=%s",
                        user_id,
                        name,
                        type(exc).__name__,
                    )

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ):
        workspace = await super().get_workspace(
            user_id,
            agent_id,
            session_id,
            workspace_id,
        )
        self._use_huiyan_backend(workspace)
        await self._sync_workspace_mcps(
            workspace,
            user_id,
            agent_id,
            session_id,
        )
        return workspace

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ):
        workspace = await super().create_workspace(
            user_id,
            agent_id,
            session_id,
        )
        self._use_huiyan_backend(workspace)
        await self._sync_workspace_mcps(
            workspace,
            user_id,
            agent_id,
            session_id,
        )
        return workspace


class HuiyanBackgroundTaskManager(BackgroundTaskManager):
    """后台工具任务适配类型，保留 AgentScope 的 bus 取消和状态语义。"""


class HuiyanSchedulerManager(SchedulerManager):
    """计划任务适配类型，使用 AgentScope Storage 与 MessageBus。"""


__all__ = [
    "HuiyanBackgroundTaskManager",
    "HuiyanLocalBackend",
    "HuiyanMCPClient",
    "HuiyanSchedulerManager",
    "HuiyanWorkspaceManager",
]
