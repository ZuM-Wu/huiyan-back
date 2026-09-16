"""PT 检查点安全加载开关。

项目约束：PT 模型仅允许 `safe_only=True` 安全加载，禁止不安全 pickle 回退。
Ultralytics 的安全加载由进程级环境变量 `ULTRALYTICS_SAFE_LOAD` 在首次导入时定型，
因此调用方必须在导入 ultralytics 之前调用 `enable_safe_pt_loading()`；
真正的模型加载点使用 `require_safe_pt_loading()` 做拒绝式校验。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SAFE_LOAD_ENV = "ULTRALYTICS_SAFE_LOAD"


def enable_safe_pt_loading() -> bool:
    """打开 weights_only 安全加载并返回是否已生效。

    入口模块（main.py）与测试 conftest 在最早位置调用本函数；
    若 ultralytics 已先被导入，`SAFE_LOAD` 常量已定型，此时返回 False，
    由 `require_safe_pt_loading()` 在真实加载前拒绝执行。
    """
    os.environ[SAFE_LOAD_ENV] = "1"
    try:
        from ultralytics.utils import SAFE_LOAD
    except Exception:  # pragma: no cover - 依赖缺失时交由调用方按需处理
        logger.warning("[PT安全加载] 未安装 ultralytics，跳过安全加载校验")
        return False
    if not SAFE_LOAD:
        logger.warning(
            "[PT安全加载] ultralytics 先于安全加载开关导入，本次进程未启用 weights_only 加载"
        )
        return False
    return True


def require_safe_pt_loading() -> None:
    """加载 PT 权重前强制校验安全加载已生效。

    Raises:
        RuntimeError: 安全加载未生效，拒绝以不安全 pickle 路径加载检查点。
    """
    if enable_safe_pt_loading():
        return
    raise RuntimeError(
        "PT 模型仅允许 safe_only 安全加载：请确认进程入口已设置 "
        f"{SAFE_LOAD_ENV}=1（main.py 启动时自动设置），再重新加载模型"
    )
