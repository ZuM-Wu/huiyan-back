"""
通知发送 API（供业务模块调用）
统一入口，内部委托核心 NoticeSender 完成模板选择、变量替换与异步发送
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth.middleware_chain import check_admin
from core.notice_sender import notice_sender
from core.response import ok
from schemas.notice import NoticeSendRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/core/v1/notice", tags=["通知发送"])


@router.post("/send")
async def send_notice(
    data: NoticeSendRequest,
    request: Request,
    _: None = Depends(check_admin),
):
    """
    统一通知发送入口

    供业务模块（farmer、certification 等）调用
    示例用法:
        POST /api/core/v1/notice/send
        {
          "action_key": "user_registered",
          "recipient": "13800138000",
          "recipient_type": "sms",
          "variables": {"code": "123456"},
          "recipient_id": 1001
        }

    内部流程:
        1. NoticeSender 查询动作配置并选择模板
        2. 变量替换（短信 @var(key) / 邮件 {key}）
        3. 写入 hy_notice_log 日志（pending）
        4. 异步投递插件发送任务，worker 回写发送结果
        5. 动作开启 trigger_inbox 时联动生成站内信
    """
    try:
        result = await notice_sender.send(
            action_key=data.action_key,
            recipient=data.recipient,
            channel=data.recipient_type,
            variables=data.variables or {},
            recipient_id=data.recipient_id or 0,
        )
    except ValueError as e:
        # 业务校验失败（动作不存在/未启用/模板未配置等）
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[通知发送] 发送失败：{e}")
        raise HTTPException(status_code=500, detail=f"发送失败：{e}")

    return ok({"task_id": result["task_id"], "log_id": result["log_id"]}, msg="发送成功")
