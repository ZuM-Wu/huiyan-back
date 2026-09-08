# -*- coding: utf-8 -*-
"""使用 Ultralytics 安全读取模型类别标签。"""

import ast
import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class ModelLabelError(ValueError):
    """模型无法读取，或未提供有效的类别标签。"""


class ModelDependencyError(ModelLabelError):
    """读取模型前所需的 Ultralytics/OpenCV 运行依赖不可用。"""


_RUNTIME_LIBRARY_PACKAGES = {
    "libGL.so.1": "libgl1",
    "libglib-2.0.so.0": "libglib2.0-0",
    "libgomp.so.1": "libgomp1",
}


def _dependency_error(exc: ImportError | OSError) -> ModelDependencyError:
    """将导入阶段的系统库异常转换为可执行的部署提示。"""
    error_text = str(exc)
    for library, package in _RUNTIME_LIBRARY_PACKAGES.items():
        if library in error_text:
            return ModelDependencyError(
                f"服务器缺少运行库 {library}，请安装系统包 {package} 后重试"
            )
    if isinstance(exc, ModuleNotFoundError) and (
        exc.name == "ultralytics" or (exc.name or "").startswith("ultralytics.")
    ):
        return ModelDependencyError(
            "服务器未安装或无法导入 Ultralytics，请检查当前 Python 环境的依赖"
        )
    return ModelDependencyError(
        "服务器无法加载 Ultralytics/OpenCV 运行依赖，请检查 Python 依赖和系统库"
    )


def _load_torch_safe_load():
    """仅在实际读取 PT 时导入 Ultralytics 安全加载器。"""
    try:
        from ultralytics.nn.tasks import torch_safe_load
    except (ImportError, OSError) as exc:
        raise _dependency_error(exc) from exc
    return torch_safe_load


def _load_onnx_metadata_reader():
    """仅在实际读取 ONNX 时导入 Ultralytics 元数据读取器。"""
    try:
        from ultralytics.nn.backends.base import BaseBackend
    except (ImportError, OSError) as exc:
        raise _dependency_error(exc) from exc
    return BaseBackend.read_metadata


def _normalize_labels(names: Any) -> list[str]:
    """将 Ultralytics 的字典或列表类别名规范为有序字符串数组。"""
    if isinstance(names, str):
        raw_names = names.strip()
        if not raw_names:
            raise ModelLabelError("模型没有可识别的类别标签")
        try:
            names = json.loads(raw_names)
        except (json.JSONDecodeError, TypeError):
            try:
                names = ast.literal_eval(raw_names)
            except (SyntaxError, ValueError, TypeError) as exc:
                raise ModelLabelError("模型类别标签元数据格式无效") from exc
    if isinstance(names, dict):
        numbered_items: list[tuple[int, Any]] = []
        for key, value in names.items():
            try:
                numbered_items.append((int(key), value))
            except (TypeError, ValueError):
                numbered_items = []
                break
        values = (
            [value for _, value in sorted(numbered_items)]
            if numbered_items
            else list(names.values())
        )
    elif isinstance(names, (list, tuple)):
        values = list(names)
    else:
        raise ModelLabelError("模型没有可识别的类别标签")

    labels = [str(value).strip() for value in values]
    if not labels or any(not label for label in labels):
        raise ModelLabelError("模型没有可识别的类别标签")
    if len(labels) > 10000:
        raise ModelLabelError("模型类别标签数量超过 10000")
    return labels


def _extract_pt_labels(path: Path) -> list[str]:
    """以 Ultralytics 受限反序列化模式读取 PT 检查点标签。"""
    torch_safe_load = _load_torch_safe_load()
    checkpoint, _ = torch_safe_load(str(path), safe_only=True)
    if not isinstance(checkpoint, dict):
        raise ModelLabelError("PT 文件不是有效的 Ultralytics 检查点")
    model = checkpoint.get("ema")
    if model is None:
        model = checkpoint.get("model")
    names = getattr(model, "names", None) if model is not None else None
    if names is None and isinstance(model, dict):
        names = model.get("names")
    if names is None:
        names = checkpoint.get("names")
    return _normalize_labels(names)


def _extract_onnx_labels(path: Path) -> list[str]:
    """通过 Ultralytics 元数据读取器提取 ONNX 标签，不创建推理会话。"""
    read_metadata = _load_onnx_metadata_reader()
    metadata = read_metadata(path)
    return _normalize_labels(metadata.get("names"))


def extract_model_labels(path: Path) -> list[str]:
    """按模型格式提取标签，不执行推理。"""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pt":
            return _extract_pt_labels(path)
        if suffix == ".onnx":
            return _extract_onnx_labels(path)
    except ModelLabelError:
        raise
    except Exception as exc:
        raise ModelLabelError("模型文件无效或缺少类别元数据") from exc
    raise ModelLabelError("仅支持 PT 或 ONNX 模型")


def _resolve_model_label(names: object, class_id: int) -> str:
    """解析推理类别标签，元数据缺失时返回可追踪的数字标签。"""
    value: object | None = None
    if isinstance(names, dict):
        value = names.get(class_id)
        if value is None:
            value = names.get(str(class_id))
    elif isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        value = names[class_id]
    else:
        try:
            value = names[class_id]  # type: ignore[index]
        except (IndexError, KeyError, TypeError):
            value = None
    label = str(value).strip() if value is not None else ""
    if not label:
        logger.warning(
            "[yolo_model_manager] 推理结果缺少类别标签，使用类别编号: class_id=%d",
            class_id,
        )
        return str(class_id)
    return label
