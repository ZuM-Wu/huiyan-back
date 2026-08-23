"""
官网配置管理 API

提供管理员对官网公开配置的写入接口：
    POST /api/admin/v1/site/config   写入官网导航 / 页脚 / Banner / 简介

读取走公开接口 GET /api/site/v1/config（无需登录）。
写入仅限 admin 角色，校验 JSON 结构合法性后落 hy_config。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.config_service import (
    get_config as svc_get_config,
    set_config as svc_set_config,
    update_site_config as svc_update_site_config,
)
from core.response import ok
from core.log.active_log import active_log

# 官网配置 router（前缀 /api/admin/v1/site）
router = APIRouter(prefix="/api/admin/v1/site", tags=["官网配置"])

# ---------------------------------------------------------------------------
# 允许的字段 key 及其默认值（JSON 字符串）与描述
# ---------------------------------------------------------------------------
_FIELDS = {
    "nav":    {"default": "[]",  "desc": "官网导航菜单（JSON 数组）",        "kind": "list"},
    "footer": {"default": "[]",  "desc": "官网页脚分组（JSON 数组）",        "kind": "list"},
    "banner": {"default": "{}",  "desc": "官网 Banner（JSON 对象）",        "kind": "dict"},
    "intro":  {"default": "",    "desc": "官网功能简介文案（纯文本 / Markdown 子集）", "kind": "text"},
}


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------
async def _read_json(key: str, default="[]"):
    """从 hy_config 读取 JSON 配置项并解析为 Python 对象"""
    raw = await svc_get_config(key)
    if not raw:
        return json.loads(default)
    try:
        return json.loads(raw)
    except Exception:
        return json.loads(default)


async def _write_json(key: str, value, desc: str = ""):
    """将 Python 对象序列化后写入 hy_config"""
    await svc_set_config(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _validate_index(items: list, index: int, label: str = "项目"):
    """校验数组下标合法性"""
    if index < 0 or index >= len(items):
        raise HTTPException(status_code=404, detail=f"{label}不存在（下标 {index}）")


# ===================================================================
# 批量配置读写（原有接口）
# ===================================================================

@router.post("/config", dependencies=[Depends(require_admin_permission("site:update"))])
async def update_site_config(data: dict, request: Request):
    """
    批量写入官网配置项（部分更新也允许）。

    请求体示例:
        {
            "nav":    [{"label": "首页", "href": "/", "target": "_self"}],
            "banner": {"title": "慧眼护农", "subtitle": "...", "background": "..."},
            "intro":  "功能介绍文案",
            "footer": [{"title": "产品", "links": [{"label": "...", "href": "..."}]}]
        }
    """
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=400, detail="请求体必须为非空对象")

    # 校验字段合法性
    unknown = [k for k in data.keys() if k not in _FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的字段: {', '.join(unknown)}")

    # 持久化（service 层负责 JSON 规范化和 site_ 前缀）
    try:
        await svc_update_site_config(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await active_log("更新官网配置", log_type="site_config", request=request)
    return ok({
        "message": "官网配置已更新",
        "updated": [f"site_{k}" for k in data.keys()],
    })


@router.get("/config")
async def get_site_config(_: None = Depends(check_admin)):
    """管理员读取当前官网配置（与公开 API 一致但需登录）"""
    raw = {}
    for key, meta in _FIELDS.items():
        val = await svc_get_config(f"site_{key}")
        raw[key] = val or meta["default"]
    return ok({
        "nav":    json.loads(raw["nav"]),
        "footer": json.loads(raw["footer"]),
        "banner": json.loads(raw["banner"]),
        "intro":  raw["intro"],
    })


# ===================================================================
# 导航项 CRUD（操作 site_nav JSON 数组的单个元素）
# ===================================================================

@router.post("/nav", dependencies=[Depends(require_admin_permission("site:update"))])
async def add_nav_item(data: dict, request: Request):
    """新增一条官网导航项"""
    nav = await _read_json("site_nav")
    item = {
        "label": str(data.get("label", "")).strip(),
        "href": str(data.get("href", "")).strip(),
        "target": data.get("target", "_self"),
        "visible": int(data.get("visible", 1)),
    }
    if not item["label"]:
        raise HTTPException(status_code=400, detail="导航名称不可为空")
    if not item["href"]:
        raise HTTPException(status_code=400, detail="链接地址不可为空")
    nav.append(item)
    await _write_json("site_nav", nav, _FIELDS["nav"]["desc"])
    await active_log("新增官网导航项", log_type="site_nav_create", request=request)
    return ok({"index": len(nav) - 1}, msg="导航项已添加")


@router.put("/nav/{index}", dependencies=[Depends(require_admin_permission("site:update"))])
async def update_nav_item(index: int, data: dict, request: Request):
    """编辑指定下标的导航项"""
    nav = await _read_json("site_nav")
    _validate_index(nav, index, "导航项")
    nav[index] = {
        "label": str(data.get("label", nav[index].get("label", ""))).strip(),
        "href": str(data.get("href", nav[index].get("href", ""))).strip(),
        "target": data.get("target", nav[index].get("target", "_self")),
        "visible": int(data.get("visible", nav[index].get("visible", 1))),
    }
    if not nav[index]["label"]:
        raise HTTPException(status_code=400, detail="导航名称不可为空")
    await _write_json("site_nav", nav, _FIELDS["nav"]["desc"])
    await active_log(f"更新官网导航项: {index}", log_type="site_nav_update", request=request)
    return ok(msg="导航项已更新")


@router.delete("/nav/{index}", dependencies=[Depends(require_admin_permission("site:update"))])
async def delete_nav_item(index: int, request: Request):
    """删除指定下标的导航项"""
    nav = await _read_json("site_nav")
    _validate_index(nav, index, "导航项")
    nav.pop(index)
    await _write_json("site_nav", nav, _FIELDS["nav"]["desc"])
    await active_log(f"删除官网导航项: {index}", log_type="site_nav_delete", request=request)
    return ok(msg="导航项已删除")


# ===================================================================
# 页脚分组 + 子链接 CRUD（操作 site_footer JSON 数组）
# ===================================================================

@router.post("/footer", dependencies=[Depends(require_admin_permission("site:update"))])
async def add_footer_group(data: dict, request: Request):
    """新增一个页脚分组"""
    footer = await _read_json("site_footer")
    title = str(data.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=400, detail="分组标题不可为空")
    footer.append({"title": title, "links": []})
    await _write_json("site_footer", footer, _FIELDS["footer"]["desc"])
    await active_log("新增官网页脚分组", log_type="site_footer_create", request=request)
    return ok({"index": len(footer) - 1}, msg="页脚分组已添加")


@router.put("/footer/{index}", dependencies=[Depends(require_admin_permission("site:update"))])
async def update_footer_group(index: int, data: dict, request: Request):
    """编辑页脚分组标题"""
    footer = await _read_json("site_footer")
    _validate_index(footer, index, "页脚分组")
    title = str(data.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=400, detail="分组标题不可为空")
    footer[index]["title"] = title
    await _write_json("site_footer", footer, _FIELDS["footer"]["desc"])
    await active_log(f"更新官网页脚分组: {index}", log_type="site_footer_update", request=request)
    return ok(msg="页脚分组已更新")


@router.delete("/footer/{index}", dependencies=[Depends(require_admin_permission("site:update"))])
async def delete_footer_group(index: int, request: Request):
    """删除页脚分组（含其全部链接）"""
    footer = await _read_json("site_footer")
    _validate_index(footer, index, "页脚分组")
    footer.pop(index)
    await _write_json("site_footer", footer, _FIELDS["footer"]["desc"])
    await active_log(f"删除官网页脚分组: {index}", log_type="site_footer_delete", request=request)
    return ok(msg="页脚分组已删除")


@router.post("/footer/{index}/link", dependencies=[Depends(require_admin_permission("site:update"))])
async def add_footer_link(index: int, data: dict, request: Request):
    """在指定分组内新增一条链接"""
    footer = await _read_json("site_footer")
    _validate_index(footer, index, "页脚分组")
    link = {
        "label": str(data.get("label", "")).strip(),
        "href": str(data.get("href", "")).strip(),
    }
    if not link["label"]:
        raise HTTPException(status_code=400, detail="链接名称不可为空")
    if not link["href"]:
        raise HTTPException(status_code=400, detail="链接地址不可为空")
    footer[index].setdefault("links", []).append(link)
    await _write_json("site_footer", footer, _FIELDS["footer"]["desc"])
    await active_log(f"新增官网页脚链接: {index}", log_type="site_footer_link_create", request=request)
    return ok(msg="链接已添加")


@router.put("/footer/{index}/link/{li}", dependencies=[Depends(require_admin_permission("site:update"))])
async def update_footer_link(index: int, li: int, data: dict, request: Request):
    """编辑指定分组内的链接"""
    footer = await _read_json("site_footer")
    _validate_index(footer, index, "页脚分组")
    links = footer[index].get("links", [])
    _validate_index(links, li, "链接")
    links[li] = {
        "label": str(data.get("label", links[li].get("label", ""))).strip(),
        "href": str(data.get("href", links[li].get("href", ""))).strip(),
    }
    if not links[li]["label"]:
        raise HTTPException(status_code=400, detail="链接名称不可为空")
    await _write_json("site_footer", footer, _FIELDS["footer"]["desc"])
    await active_log(f"更新官网页脚链接: {index}/{li}", log_type="site_footer_link_update", request=request)
    return ok(msg="链接已更新")


@router.delete("/footer/{index}/link/{li}", dependencies=[Depends(require_admin_permission("site:update"))])
async def delete_footer_link(index: int, li: int, request: Request):
    """删除指定分组内的链接"""
    footer = await _read_json("site_footer")
    _validate_index(footer, index, "页脚分组")
    links = footer[index].get("links", [])
    _validate_index(links, li, "链接")
    links.pop(li)
    await _write_json("site_footer", footer, _FIELDS["footer"]["desc"])
    await active_log(f"删除官网页脚链接: {index}/{li}", log_type="site_footer_link_delete", request=request)
    return ok(msg="链接已删除")
