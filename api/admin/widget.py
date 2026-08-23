"""Widget 挂件 API — 仪表盘挂件管理接口"""
from fastapi import APIRouter, Depends, Request

from core.auth.middleware_chain import check_admin
from services.widget.widget_engine import widget_engine
from core.response import ok, fail

router = APIRouter(prefix="/api/admin/v1/widget", tags=["仪表盘挂件"])


@router.get("/dashboard")
async def widget_dashboard(request: Request, _: None = Depends(check_admin)):
    """
    获取首页仪表盘全部数据
    一次返回：所有挂件元信息 + 当前管理员显示列表 + 挂件渲染数据
    """
    admin_id = request.state.user_id
    data = await widget_engine.get_dashboard(admin_id)
    return ok(data)


@router.get("/list")
async def widget_list(_: None = Depends(check_admin)):
    """获取所有已注册挂件元信息"""
    return ok({"list": await widget_engine.get_widget_list()})


@router.put("/order")
async def widget_order(request: Request, data: dict, _: None = Depends(check_admin)):
    """
    保存挂件排序
    请求体: {widgets: ["name1", "name2", ...]} — 有序数组，顺序即排序
    """
    admin_id = request.state.user_id
    widgets = data.get("widgets", [])
    if not isinstance(widgets, list):
        return fail(400, "widgets 必须是数组")
    await widget_engine.save_widget_config(admin_id, widgets)
    return ok(msg="排序已保存")


@router.put("/toggle")
async def widget_toggle(request: Request, data: dict, _: None = Depends(check_admin)):
    """
    切换挂件显示/隐藏
    请求体: {widget: "挂件标识", status: 1显示/0隐藏}
    """
    admin_id = request.state.user_id
    widget_name = data.get("widget", "")
    status = data.get("status", 1)

    if not widget_name:
        return fail(400, "widget 参数不能为空")

    show_list = await widget_engine.toggle_widget(
        admin_id, widget_name, enabled=(status == 1)
    )
    return ok({"show_widgets": show_list}, msg="状态已更新")
