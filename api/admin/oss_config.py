"""
对象存储配置管理 API

对标 ZJMF configuration_oss 页面后端，提供 OSS 插件列表、连通检测、
切换存储方式（需管理员密码二次验证）三个端点。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.auth.password import verify_password
from core.config_service import get_config, set_config
from core.response import ok, fail
from core.admin_service import get_admin_by_id, get_admin_by_username
from core.plugin_query_service import list_all_plugins, get_plugin_by_name
from core.log.active_log import active_log
from core.oss_service import oss_service
from core.plugin_manager import PluginManager
from services.storage_migration import create_scan_job, get_job, get_latest_job, start_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1/oss", tags=["对象存储配置"])

# local_oss 内置插件默认元数据（hy_plugin 表无记录时补充虚拟记录）
_LOCAL_OSS_DEFAULT = {
    "name": "local_oss",
    "title": "本地存储",
    "version": "1.0.0",
    "author": "HuiYan Team",
    "status": 1,
    "capabilities": ["storage.backend"],
    "configurable": False,
    "management": False,
}


class SwitchRequest(BaseModel):
    """切换存储方式请求体"""
    oss_method: str
    password: str


def _capability_info(pm: PluginManager, name: str, fallback: dict | None = None) -> dict:
    """从 manifest 和配置 Schema 提取通用存储能力信息。"""
    meta = fallback or {}
    try:
        meta = pm.load_metadata(name)
        schema = pm.get_config_schema(name)
    except Exception:
        schema = []
    capabilities = list(meta.get("capabilities") or [])
    return {
        "capabilities": capabilities,
        "configurable": bool(schema),
        "management": "storage.management" in capabilities,
    }


@router.get("/list")
async def list_oss_plugins(_: None = Depends(check_admin)):
    """
    列出所有 OSS 插件 + 当前存储方式

    返回已安装插件（hy_plugin 表）和未安装插件（文件系统扫描），
    local_oss 为内置默认安装，始终显示。
    """
    # 读取当前存储方式
    current_method = await get_config("oss_method") or "local_oss"

    # 查询已安装的 oss 插件（通过 service 层获取全量列表后过滤）
    all_plugins = await list_all_plugins()
    installed_plugins = [p for p in all_plugins if p["module"] == "oss"]

    # 构建已安装插件字典 {name: plugin_dict}
    installed_map = {p["name"]: p for p in installed_plugins}

    # local_oss 为内置默认安装，若表中无记录则补充虚拟记录
    if "local_oss" not in installed_map:
        installed_map["local_oss"] = dict(_LOCAL_OSS_DEFAULT)

    # 扫描文件系统，发现未安装的 oss 插件
    pm = PluginManager()
    discovered = pm.discover()
    discovered_oss = [d for d in discovered if d["module"] == "oss"]

    # 合并：已安装的用表记录，未安装的用 plugin.json 元数据
    plugin_list = []
    for name, record in installed_map.items():
        info = _capability_info(pm, name, record)
        plugin_list.append({
            "name": record["name"],
            "title": record["title"],
            "version": record["version"],
            "author": record.get("author", "") or "",
            "status": record["status"],
            "has_data": False,  # 默认 False，下方按需查询
            "is_current": record["name"] == current_method,
            **info,
        })

    # 补充未安装的插件（status=3）
    installed_names = {p["name"] for p in plugin_list}
    for d in discovered_oss:
        if d["name"] not in installed_names:
            try:
                meta = pm.load_metadata(d["name"])
                plugin_list.append({
                    "name": d["name"],
                    "title": meta.get("title", d["name"]),
                    "version": meta.get("version", "1.0.0"),
                    "author": meta.get("author", ""),
                    "status": 3,  # 未安装
                    "has_data": False,
                    "is_current": d["name"] == current_method,
                    **_capability_info(pm, d["name"], meta),
                })
            except Exception:
                logger.warning("[OSS配置] 读取插件 %s 元数据失败", d["name"])

    # 对已启用插件查询是否存在数据
    for item in plugin_list:
        if item["status"] == 1:
            try:
                plugin = await oss_service.get_active_plugin()
                if plugin and plugin.name == item["name"]:
                    result = await plugin.oss_has_data()
                    item["has_data"] = result.get("data", {}).get("has_data", False)
            except Exception as e:
                logger.warning("[OSS配置] 查询插件 %s 数据存在性失败: %s", item["name"], e)

    return ok({
        "current_method": current_method,
        "list": plugin_list,
    })


@router.post("/test", dependencies=[Depends(require_admin_permission("oss:test"))])
async def test_connection():
    """
    连通检测 — 测试当前激活的存储插件是否可用

    返回 {"success": true/false, "message": "..."}
    """
    plugin = await oss_service.get_active_plugin()
    if not plugin:
        return fail(400, "存储插件不可用")

    try:
        result = await plugin.oss_link()
        if result.get("status") == "success":
            return ok(msg=result.get("msg", "连接正常"))
        return fail(400, result.get("msg", "连接失败"))
    except Exception as e:
        logger.warning("[OSS配置] 连通检测异常: %s", e)
        return fail(400, str(e))


@router.put("/switch", dependencies=[Depends(require_admin_permission("oss:switch"))])
async def switch_storage_method(
    data: SwitchRequest,
    request: Request,
):
    """
    切换存储方式 — 需管理员密码二次验证

    1. 验证管理员密码
    2. 校验目标插件已启用
    3. 写入 oss_method 配置
    4. 失效 OssService 缓存
    """
    # 密码验证（对标 ZJMF idcsmart_password_compare）
    admin_id = request.state.user_id
    admin = await get_admin_by_id(admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="管理员不存在")

    # 获取含密码哈希的完整信息（admin_service.get_admin_by_id 不返回 password）
    admin_with_pw = await get_admin_by_username(admin["username"])
    if not admin_with_pw:
        raise HTTPException(status_code=404, detail="管理员不存在")

    if not verify_password(data.password, admin_with_pw["password"]):
        raise HTTPException(status_code=400, detail="管理员密码不正确")

    # 校验目标插件已安装且已启用
    target = await get_plugin_by_name(data.oss_method)

    # local_oss 为内置插件，跳过表校验
    if data.oss_method != "local_oss":
        if not target or target.get("module") != "oss":
            raise HTTPException(status_code=400, detail="目标存储插件未安装")
        if target.get("status") != 1:
            raise HTTPException(status_code=400, detail="目标存储插件未启用")

    # 写入 oss_method 配置
    await set_config("oss_method", data.oss_method)

    # 失效 OssService 进程内缓存
    oss_service.invalidate()

    # 记录操作日志
    await active_log(
        description=f"切换对象存储方式为: {data.oss_method}",
        log_type="config",
        request=request,
    )

    return ok({"oss_method": data.oss_method}, msg="存储方式已切换")


@router.post("/migrations/scan", dependencies=[Depends(require_admin_permission("oss:migrate"))])
async def scan_storage_migration(target_method: str):
    """创建或恢复公共文件同步任务，扫描完成后自动入队。"""
    target = await get_plugin_by_name(target_method)
    if not target or target.get("module") != "oss" or target.get("status") != 1:
        raise HTTPException(status_code=400, detail="目标存储插件未安装或未启用")
    try:
        job_id = await create_scan_job(target_method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok({"job_id": job_id}, msg="本地文件同步任务已提交")


@router.get("/migrations/latest", dependencies=[Depends(require_admin_permission("oss:migrate"))])
async def latest_storage_migration(target_method: str = ""):
    """查询目标存储最近一次公共文件迁移任务。"""
    return ok(await get_latest_job(target_method or None))


@router.get("/migrations/{job_id}", dependencies=[Depends(require_admin_permission("oss:migrate"))])
async def storage_migration_detail(
    job_id: int,
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(20, ge=1, le=100),
):
    """查询公共文件迁移任务进度和失败明细。"""
    result = await get_job(job_id, page=page, limit=limit)
    if not result:
        raise HTTPException(status_code=404, detail="迁移任务不存在")
    return ok(result)


@router.post("/migrations/{job_id}/start", dependencies=[Depends(require_admin_permission("oss:migrate"))])
async def start_storage_migration(job_id: int):
    """兼容旧 awaiting 迁移任务的手动入队入口。"""
    try:
        task_id = await start_job(job_id, retry=False)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not task_id:
        raise HTTPException(status_code=409, detail="迁移任务入队失败，请查看任务详情")
    return ok({"job_id": job_id, "task_id": task_id}, msg="迁移任务已入队")


@router.post("/migrations/{job_id}/retry", dependencies=[Depends(require_admin_permission("oss:migrate"))])
async def retry_storage_migration(job_id: int):
    """重新入队包含失败明细的迁移任务。"""
    try:
        task_id = await start_job(job_id, retry=True)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not task_id:
        raise HTTPException(status_code=409, detail="迁移任务入队失败，请查看任务详情")
    return ok({"job_id": job_id, "task_id": task_id}, msg="失败文件已重新入队")
