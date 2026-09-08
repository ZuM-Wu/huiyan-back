"""硬件地图标记的地块内坐标计算。"""

import json
import math
from collections.abc import Sequence

Point = tuple[float, float]


def parse_plot_boundary(boundary: str | dict | None) -> list[Point]:
    """解析地块 GeoJSON 外环，过滤无法转换为有限坐标的点。"""
    if not boundary:
        return []
    try:
        geo = json.loads(boundary) if isinstance(boundary, str) else boundary
        coordinates = geo.get("coordinates", []) if geo.get("type") == "Polygon" else []
        ring = coordinates[0] if coordinates else []
        points = [(float(item[0]), float(item[1])) for item in ring]
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []
    points = [point for point in points if math.isfinite(point[0]) and math.isfinite(point[1])]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points if len(points) >= 3 else []


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    """将边线上的点视为地块内，避免浮点误差拒绝贴近边界的合法拖动。"""
    cross = ((point[1] - start[1]) * (end[0] - start[0])
             - (point[0] - start[0]) * (end[1] - start[1]))
    if abs(cross) > 1e-10:
        return False
    return (
        min(start[0], end[0]) - 1e-10 <= point[0] <= max(start[0], end[0]) + 1e-10
        and min(start[1], end[1]) - 1e-10 <= point[1] <= max(start[1], end[1]) + 1e-10
    )


def point_in_polygon(point: Point, path: Sequence[Point]) -> bool:
    """使用射线法判断坐标是否位于地块外环内部或边线上。"""
    if len(path) < 3:
        return False
    inside = False
    previous = path[-1]
    for current in path:
        if _point_on_segment(point, previous, current):
            return True
        if ((current[1] > point[1]) != (previous[1] > point[1])):
            crossing = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < crossing:
                inside = not inside
        previous = current
    return inside


def _polygon_centroid(path: Sequence[Point]) -> Point:
    """计算多边形面积重心；退化外环回退到顶点平均值。"""
    area_twice = 0.0
    longitude_sum = 0.0
    latitude_sum = 0.0
    for index, start in enumerate(path):
        end = path[(index + 1) % len(path)]
        cross = start[0] * end[1] - end[0] * start[1]
        area_twice += cross
        longitude_sum += (start[0] + end[0]) * cross
        latitude_sum += (start[1] + end[1]) * cross
    if abs(area_twice) > 1e-12:
        return (
            longitude_sum / (3 * area_twice),
            latitude_sum / (3 * area_twice),
        )
    return (
        sum(point[0] for point in path) / len(path),
        sum(point[1] for point in path) / len(path),
    )


def _point_at_ratio(path: Sequence[Point], ratio: float) -> Point:
    """按旧版外环比例取点，仅用于把存量边线标记平滑移入地块。"""
    segments = []
    total = 0.0
    for index, start in enumerate(path):
        end = path[(index + 1) % len(path)]
        length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        segments.append((start, end, length, total))
        total += length
    target = max(0.0, min(1.0, ratio)) * total
    for start, end, length, offset in segments:
        if target <= offset + length:
            progress = (target - offset) / length if length else 0.0
            return (
                start[0] + (end[0] - start[0]) * progress,
                start[1] + (end[1] - start[1]) * progress,
            )
    return path[0]


def _grid_interior_point(path: Sequence[Point], preferred: Point) -> Point | None:
    """为重心落在凹多边形外的情况，从规则网格中选择最接近重心的内部点。"""
    min_lng = min(point[0] for point in path)
    max_lng = max(point[0] for point in path)
    min_lat = min(point[1] for point in path)
    max_lat = max(point[1] for point in path)
    candidates = [
        (min_lng + (max_lng - min_lng) * x / 12,
         min_lat + (max_lat - min_lat) * y / 12)
        for x in range(1, 12)
        for y in range(1, 12)
    ]
    candidates.sort(key=lambda item: (
        (item[0] - preferred[0]) ** 2 + (item[1] - preferred[1]) ** 2
    ))
    return next((item for item in candidates if point_in_polygon(item, path)), None)


def choose_interior_point(
    boundary: str | dict | None,
    legacy_ratio: float | None = None,
) -> Point | None:
    """选择稳定的地块内默认点，并让旧边线比例沿重心方向内移。"""
    path = parse_plot_boundary(boundary)
    if not path:
        return None
    centroid = _polygon_centroid(path)
    preferred = centroid if point_in_polygon(centroid, path) else None
    if preferred is None:
        preferred = _grid_interior_point(path, centroid)
    if preferred is None:
        return path[0]
    if legacy_ratio is None:
        return preferred
    edge = _point_at_ratio(path, legacy_ratio)
    for progress in (0.2, 0.35, 0.5, 0.75, 1.0):
        candidate = (
            edge[0] + (preferred[0] - edge[0]) * progress,
            edge[1] + (preferred[1] - edge[1]) * progress,
        )
        if point_in_polygon(candidate, path):
            return candidate
    return preferred


def resolve_marker_position(
    boundary: str | dict | None,
    longitude: float | None,
    latitude: float | None,
    legacy_ratio: float | None = None,
) -> Point | None:
    """优先使用已保存坐标；边界变化导致越界时回退到新的地块内默认点。"""
    path = parse_plot_boundary(boundary)
    if longitude is not None and latitude is not None:
        saved = (float(longitude), float(latitude))
        if point_in_polygon(saved, path):
            return saved
    return choose_interior_point(boundary, legacy_ratio)
