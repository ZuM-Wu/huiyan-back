"""菜单管理 API"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.admin_service import get_admin_permissions
from core.auth.middleware_chain import check_admin
from core.auth.rbac import filter_menu_tree_by_permissions, require_admin_permission
from core.menu_service import (
    create_menu as svc_create_menu,
    delete_menu as svc_delete_menu,
    list_menus as svc_list_menus,
    list_navs as svc_list_navs,
    update_menu as svc_update_menu,
)
from core.plugin_query_service import list_all_plugins
from core.response import ok
from core.log.active_log import active_log

router = APIRouter(prefix="/api/admin/v1/menu", tags=["菜单管理"])


class MenuCreate(BaseModel):
    """创建菜单请求体"""
    name: str = Field(..., min_length=1, max_length=64, description="菜单标识")
    title: str = Field(..., min_length=1, max_length=128, description="菜单标题")
    path: str = Field(default="", max_length=256, description="前端路由路径")
    icon: str = Field(default="", max_length=64, description="图标名称")
    parent_id: int = Field(default=0, description="父菜单ID，0=顶级")
    sort_order: int = Field(default=0, description="排序")
    plugin: str = Field(default="", max_length=64, description="所属插件标识")
    visible: int = Field(default=1, description="是否可见: 0=隐藏, 1=显示")
    nav_type: str = Field(default="admin", max_length=32, description="导航类型: admin/frontend")
    page_type: str = Field(default="system", max_length=32, description="页面类型: system/url/separator")
    target_type: str = Field(default="", max_length=16, description="外链打开方式: 空/_blank/iframe")


class MenuUpdate(BaseModel):
    """更新菜单请求体 — 仅更新传入的非 None 字段"""
    name: str | None = Field(default=None, max_length=64, description="菜单标识")
    title: str | None = Field(default=None, max_length=128, description="菜单标题")
    path: str | None = Field(default=None, max_length=256, description="前端路由路径")
    icon: str | None = Field(default=None, max_length=64, description="图标名称")
    parent_id: int | None = Field(default=None, description="父菜单ID")
    sort_order: int | None = Field(default=None, description="排序")
    plugin: str | None = Field(default=None, max_length=64, description="所属插件标识")
    visible: int | None = Field(default=None, description="是否可见")
    page_type: str | None = Field(default=None, max_length=32, description="页面类型")
    target_type: str | None = Field(default=None, max_length=16, description="外链打开方式")


class MenuReorderItem(BaseModel):
    """单个排序项"""
    id: int = Field(..., description="菜单ID")
    sort_order: int = Field(..., description="新排序值")
    parent_id: int = Field(..., description="新父菜单ID")


class MenuReorderRequest(BaseModel):
    """批量排序请求体"""
    items: List[MenuReorderItem] = Field(..., min_length=1, description="排序项列表")


def _build_menu_tree(menus: list[dict], installed_plugins: set[str] | None = None) -> list[dict]:
    """从扁平菜单列表构建树形结构（含插件过滤）

    与 core.auth.rbac.get_menu_tree 输出格式一致。
    """
    # 按已安装插件过滤
    if installed_plugins is not None:
        menus = [m for m in menus if not m["plugin"] or m["plugin"] in installed_plugins]

    menu_dict = {}
    for m in menus:
        menu_dict[m["id"]] = {
            "id": m["id"], "name": m["name"], "title": m["title"],
            "path": m["path"], "icon": m["icon"], "parent_id": m["parent_id"],
            "sort_order": m["sort_order"], "plugin": m["plugin"],
            "visible": bool(m["visible"]),
            "page_type": m.get("page_type") or "system",
            "target_type": m.get("target_type") or "",
            "children": []
        }

    tree = []
    for node in menu_dict.values():
        if node["parent_id"] == 0:
            tree.append(node)
        else:
            parent = menu_dict.get(node["parent_id"])
            if parent:
                parent["children"].append(node)
    return tree


@router.get("/tree")
async def menu_tree(nav_type: str = "admin", _: None = Depends(check_admin)):
    """获取完整菜单树，支持 nav_type 筛选（导航管理编辑器用，返回全部菜单）"""
    menus = await svc_list_menus(nav_type=nav_type)
    plugins = await list_all_plugins()
    installed = {p["name"] for p in plugins}
    tree = _build_menu_tree(menus, installed)
    return ok({"list": tree})


@router.get("/my-tree")
async def my_menu_tree(request: Request, nav_type: str = "admin", _: None = Depends(check_admin)):
    """
    获取"当前登录管理员"有权限查看的菜单树（侧边栏专用）。
    超级管理员(id=1)返回完整树；普通管理员按其角色权限过滤，无权限的菜单不下发。
    """
    user_id = getattr(request.state, "user_id", 0)
    menus = await svc_list_menus(nav_type=nav_type)
    plugins = await list_all_plugins()
    installed = {p["name"] for p in plugins}
    tree = _build_menu_tree(menus, installed)
    if user_id == 1:
        return ok({"list": tree})
    codes = await get_admin_permissions(user_id)
    return ok({"list": filter_menu_tree_by_permissions(tree, codes)})


@router.get("/registered")
async def menu_registered(nav_type: str = "admin", _: None = Depends(check_admin)):
    """返回可注册的页面列表 — 从 hy_nav 表读取（Nav/Menu 分层架构）

    hy_nav 表由两部分组成：
    - 系统预设页面（种子数据写入）
    - 插件声明页面（插件安装时自动注册）

    返回格式按 source 分组：system=系统页面，plugin=插件页面。
    """
    navs = await svc_list_navs(nav_type=nav_type)

    system_pages = []
    plugin_pages = []
    for n in navs:
        item = {
            "key": n["key"],
            "title": n["title"],
            "path": n["path"],
            "icon": n["icon"],
            "source": n["source"],
            "plugin": n["plugin"],
            "page_type": n["page_type"],
            "group": n["group_name"] or ("其他" if n["source"] == "system" else n["plugin"]),
        }
        if n["source"] == "system":
            system_pages.append(item)
        else:
            plugin_pages.append(item)

    return ok({
        "system": system_pages,
        "plugin": plugin_pages,
    })


@router.post("/create", dependencies=[Depends(require_admin_permission("menu:create"))])
async def menu_create(data: MenuCreate, request: Request):
    """创建菜单节点"""
    try:
        menu_id = await svc_create_menu(data.model_dump())
        await active_log(f"创建菜单: {data.name}", "menu_create", rel_id=menu_id, request=request)
        return ok({"id": menu_id}, msg="创建成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建菜单失败: {e!s}")


@router.put("/reorder", dependencies=[Depends(require_admin_permission("menu:reorder"))])
async def menu_reorder(data: MenuReorderRequest, request: Request):
    """批量更新菜单排序和父级"""
    try:
        for item in data.items:
            await svc_update_menu(item.id, {
                "sort_order": item.sort_order,
                "parent_id": item.parent_id,
            })
        await active_log(
            f"批量调整菜单排序: {len(data.items)}项",
            "menu_reorder", request=request,
        )
        return ok(msg="排序更新成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"排序更新失败: {e!s}")


@router.put("/{menu_id}", dependencies=[Depends(require_admin_permission("menu:update"))])
async def menu_update(menu_id: int, data: MenuUpdate, request: Request):
    """更新菜单节点"""
    try:
        update_data = data.model_dump(exclude_unset=True)
        success = await svc_update_menu(menu_id, update_data)
        if not success:
            raise HTTPException(status_code=404, detail="菜单节点不存在")
        await active_log(f"更新菜单: {menu_id}", "menu_update", rel_id=menu_id, request=request)
        return ok(msg="更新成功")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新菜单失败: {e!s}")


@router.delete("/{menu_id}", dependencies=[Depends(require_admin_permission("menu:delete"))])
async def menu_delete(menu_id: int, request: Request):
    """删除菜单节点"""
    try:
        success = await svc_delete_menu(menu_id)
        if not success:
            raise HTTPException(status_code=404, detail="菜单节点不存在")
        await active_log(f"删除菜单: {menu_id}", "menu_delete", rel_id=menu_id, request=request)
        return ok(msg="删除成功")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除菜单失败: {e!s}")
