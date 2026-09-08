"""JJR 属性合并与单位补全，厂商约定不进入公共服务。"""
from typing import Any

def _infer_property_type(identifier: str, value: Any) -> str:
    """为平台属性接口新增但标识列表未声明的字段补充展示类型。"""
    compact_identifier = identifier.lower().replace("_", "")
    if compact_identifier in {"imgurl", "imageurl", "photourl", "pictureurl"}:
        return "image"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _merge_realtime_properties(
    identifiers: list[dict],
    properties: dict[str, Any],
) -> list[dict]:
    """保留标识元数据，以实时属性覆盖值，并接纳平台新增的未知属性。"""
    merged: list[dict] = []
    seen: set[str] = set()
    for raw in identifiers:
        item = dict(raw)
        identifier = str(item.get("identifier") or "")
        if identifier and identifier in properties:
            item["value"] = properties[identifier]
        if identifier:
            seen.add(identifier)
        merged.append(item)
    for identifier, value in properties.items():
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            continue
        merged.append({
            "identifier": identifier,
            "name": identifier,
            "value": value,
            "unit": "",
            "dataType": _infer_property_type(identifier, value),
            "lastUpdateTime": "",
        })
    return merged


