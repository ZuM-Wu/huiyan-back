"""
通知参考变量 API（管理员端）
根据通知动作返回可用变量列表（分组），供模板编辑时参考插入。
"""
import logging

from fastapi import APIRouter, Depends, Query

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/v1/notice/params", tags=["通知参考变量"])

# 通用变量分组定义
# 每个分组包含 label(分组名) 和 param(变量列表)
# 变量格式: {"value": "变量名", "label": "中文描述"}
_COMMON_PARAMS = [
    {
        "label": "通用变量",
        "param": [
            {"value": "{site_name}", "label": "网站名称"},
            {"value": "{site_url}", "label": "网站地址"},
        ],
    },
]

# 按动作类型定义专属变量
_USER_PARAMS = [
    {
        "label": "用户变量",
        "param": [
            {"value": "{username}", "label": "用户名"},
            {"value": "{phone}", "label": "手机号"},
            {"value": "{email}", "label": "邮箱"},
            {"value": "{code}", "label": "验证码"},
            {"value": "{password}", "label": "新密码"},
        ],
    },
]

_ORDER_PARAMS = [
    {
        "label": "订单变量",
        "param": [
            {"value": "{order_id}", "label": "订单号"},
            {"value": "{order_amount}", "label": "订单金额"},
            {"value": "{product_name}", "label": "产品名称"},
            {"value": "{pay_time}", "label": "支付时间"},
        ],
    },
]

# 动作标识到变量分组的映射
_ACTION_PARAM_MAP = {
    "user_registered": _COMMON_PARAMS + _USER_PARAMS,
    "password_reset": _COMMON_PARAMS + _USER_PARAMS,
    "cert_approved": _COMMON_PARAMS + _USER_PARAMS,
    "cert_rejected": _COMMON_PARAMS + _USER_PARAMS,
    "order_created": _COMMON_PARAMS + _ORDER_PARAMS,
    "order_completed": _COMMON_PARAMS + _ORDER_PARAMS,
}


@router.get("/list", dependencies=[Depends(require_permission("notice:list"))])
async def list_send_params(
    action_key: str = Query(..., description="动作标识，如 user_registered"),
    _: None = Depends(check_admin),
):
    """
    根据通知动作返回可用变量列表（分组）

    前端在模板编辑页的"参考变量"按钮中使用，展示该动作支持的所有变量。
    变量格式统一为 {key}，邮件模板直接使用花括号语法，短信模板需转换为 @var(key)。
    """
    params = _ACTION_PARAM_MAP.get(action_key, _COMMON_PARAMS)
    return ok({"list": params})
