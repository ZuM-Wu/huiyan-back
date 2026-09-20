# -*- coding: utf-8 -*-
"""智能识别 addon 插件主类。"""

import asyncio
import logging
import shutil
from pathlib import Path

from core.config import BASE_DIR
from core.plugin_manager import BasePlugin

logger = logging.getLogger(__name__)
PLUGIN_NAME = "yolo_model_manager"
PLUGIN_ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = PLUGIN_ROOT / "upload"
ANNOTATED_IMAGE_DIR = BASE_DIR / "upload" / PLUGIN_NAME / "recognitions"


async def _handle_detection_task(context, data: dict) -> None:
    """延迟导入检测执行体，避免插件发现阶段加载推理依赖。"""
    from plugins.addon.yolo_model_manager.services.detection_service import (
        handle_detection_task,
    )

    await handle_detection_task(context, data)


class Plugin(BasePlugin):
    """声明模型管理插件的生命周期、权限、页面、路由和上传策略。"""

    def __init__(self, db_session=None, config: dict | None = None):
        super().__init__(db_session, config)
        self.name = PLUGIN_NAME
        self.title = "智能识别"
        self.version = "1.1.2"
        self.description = "执行生长记录仪快捷检测，并管理识别记录、YOLO模型和地块绑定"
        self.module = "addon"

    async def install(self) -> bool:
        """创建插件私有表；其余能力由 PluginManager 统一登记。"""
        if not self.db:
            return False
        await self._run_sql_file("install.sql")
        logger.info("[yolo_model_manager] 插件安装完成")
        return True

    async def uninstall(self) -> bool:
        """删除插件私有表和模型文件；平台负责清理权限、页面和配置。"""
        if not self.db:
            return False
        await self._run_sql_file("uninstall.sql")
        self._cleanup_upload_dir()
        self._cleanup_annotated_image_dir()
        logger.info("[yolo_model_manager] 插件卸载完成")
        return True

    async def upgrade(self, old_version: str) -> bool:
        """补齐旧模型标签并幂等同步当前版本权限。"""
        if not self.db:
            return False
        from sqlalchemy import select

        from plugins.addon.yolo_model_manager.models import YoloModel
        from plugins.addon.yolo_model_manager.services.label_extractor import (
            ModelLabelError,
            extract_model_labels,
        )

        rows = (await self.db.execute(select(YoloModel))).scalars().all()
        for row in rows:
            if row.labels:
                continue
            model_path = UPLOAD_DIR / row.filename
            if not model_path.is_file():
                continue
            try:
                row.labels = await asyncio.to_thread(extract_model_labels, model_path)
            except ModelLabelError as exc:
                logger.warning(
                    "[yolo_model_manager] 旧模型标签补取失败: %s, %s",
                    row.filename,
                    exc,
                )
        await self.db.flush()
        from core.auth.rbac import register_plugin_permissions
        from plugins.addon.yolo_model_manager.auth import permission_tree

        await register_plugin_permissions(PLUGIN_NAME, permission_tree, db=self.db)
        from services.agentscope.tools import sync_tool_policies

        declarations = [
            {**item, "name": f"{PLUGIN_NAME}_{item['name']}"}
            for item in self.get_mcp_tools()
        ]
        await sync_tool_policies(self.db, declarations)
        logger.info("[yolo_model_manager] 已从 %s 升级至 1.1.2", old_version)
        return True

    def get_task_definitions(self):
        """登记快捷检测的持久化任务定义。"""
        from services.task.definitions import TaskDefinition

        return [TaskDefinition(
            "yolo_recognition_detect",
            "智能识别快捷检测",
            PLUGIN_NAME,
            "yolo-recognition",
            _handle_detection_task,
            timeout_seconds=120,
            max_attempts=1,
            concurrency=1,
            backoff=False,
            failure_notifications=True,
        )]

    def get_routers(self):
        """显式声明唯一的管理端 API 路由。"""
        from plugins.addon.yolo_model_manager.router import router

        return [router]

    def get_permissions(self):
        """返回模型管理细粒度权限树。"""
        from plugins.addon.yolo_model_manager.auth import permission_tree

        return permission_tree

    def get_mcp_tools(self):
        """声明管理员识别记录与硬件历史联合分析工具。"""
        from plugins.addon.yolo_model_manager.mcp_tools import recognition_analysis
        from plugins.addon.yolo_model_manager.mcp_detection import (
            recognition_latest_result,
            recognition_plot_preview,
            recognition_plot_start,
        )

        return [{
            "name": "recognition_analysis",
            "description": (
                "读取指定智能识别记录，并按识别时间匹配该地块当前绑定全部设备"
                "每项指标的最近历史观测，返回事实、时间差、缺失项和分析边界。"
                "参数 record_id 为识别记录正整数 ID；本工具只读。"
            ),
            "handler": recognition_analysis,
            "audience": "admin",
            "permission_code": "yolo_model_manager:analysis",
        }, {
            "name": "recognition_plot_preview",
            "description": "预览地块绑定的可识别设备和有效模型。参数 plot_id 为地块ID。",
            "handler": recognition_plot_preview,
            "audience": "both",
            "permission_code": "yolo_model_manager:list",
            "required_permissions": ["hardware:list"],
        }, {
            "name": "recognition_plot_start",
            "description": "确认后按地块设备和模型发起识别任务。首次调用需确认。",
            "handler": recognition_plot_start,
            "audience": "admin",
            "permission_code": "yolo_model_manager:detect",
            "required_permissions": ["hardware:data"],
            "requires_confirmation": True,
        }, {
            "name": "recognition_latest_result",
            "description": "查询地块最新识别记录摘要。参数 plot_id 为地块ID。",
            "handler": recognition_latest_result,
            "audience": "both",
            "permission_code": "yolo_model_manager:list",
        }]

    def get_pages(self):
        """声明管理端插件页面及其本地静态资源。"""
        return [
            {
                "key": "plugin_yolo_model_manager",
                "title": "智能识别",
                "path": "/admin/plugin/yolo_model_manager/yolo_model_manager",
                "icon": "file",
                "nav_type": "admin",
                "audience": "admin",
                "template": "yolo_model_manager.html",
                "permission": "yolo_model_manager:list",
                "api_base": "/api/admin/v1/plugins/yolo_model_manager",
                "styles": ["yolo_model_manager.css"],
                "scripts": ["yolo_model_manager_helpers.js", "yolo_model_test.js", "yolo_model_manager.js"],
            }
        ]

    def get_config_schema(self):
        """模型上传限制由统一上传设置维护，不提供重复配置表单。"""
        return []

    def get_upload_policy_schema(self):
        """声明默认 500MB、允许 PT/ONNX 的可编辑上传策略。"""
        from plugins.addon.yolo_model_manager.upload_policies import POLICIES

        return POLICIES

    async def _run_sql_file(self, filename: str) -> None:
        """以 UTF-8 读取并执行插件迁移 SQL。"""
        sql_path = PLUGIN_ROOT / "migrations" / filename
        if not sql_path.is_file():
            return
        with open(sql_path, "r", encoding="utf-8") as file:
            sql = file.read()
        if sql.strip():
            await self._exec_sql(sql)

    def _cleanup_upload_dir(self) -> None:
        """仅清理经过路径校验的插件私有模型文件。"""
        upload_dir = UPLOAD_DIR.resolve()
        if upload_dir.parent != PLUGIN_ROOT or upload_dir.name != "upload":
            logger.warning("[yolo_model_manager] upload 路径异常，跳过清理: %s", upload_dir)
            return
        if not upload_dir.is_dir():
            return
        for item in upload_dir.iterdir():
            if not item.is_file() or item.name == ".gitkeep":
                continue
            try:
                item.unlink()
            except OSError:
                logger.warning("[yolo_model_manager] 模型文件清理失败: %s", item.name)

    def _cleanup_annotated_image_dir(self) -> None:
        """卸载时仅递归清理公开上传区内本插件的标注结果目录。"""
        upload_root = (BASE_DIR / "upload").resolve()
        plugin_dir = (upload_root / PLUGIN_NAME).resolve()
        result_dir = ANNOTATED_IMAGE_DIR.resolve()
        if plugin_dir.parent != upload_root or result_dir.parent != plugin_dir:
            logger.warning(
                "[yolo_model_manager] 标注图路径异常，跳过清理: %s", result_dir
            )
            return
        if result_dir.is_dir():
            try:
                shutil.rmtree(result_dir)
            except OSError:
                logger.warning(
                    "[yolo_model_manager] 标注图目录清理失败: %s", result_dir
                )
    async def repair_database(self, report: dict) -> dict | bool:
        return await self._repair_from_install_sql()
