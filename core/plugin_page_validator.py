"""插件页面及资源的安装期静态校验。"""

import re
from pathlib import Path
from urllib.parse import urlsplit

_COLOR_RE = re.compile(
    r"(?<![\w-])(?:#[0-9a-fA-F]{3,8}\b|rgba?\s*\(|hsla?\s*\()"
)
_RESOURCE_RE = re.compile(
    r"<(?:script|link)\b[^>]+?(?:src|href)\s*=\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_DUPLICATE_RUNTIME_RE = re.compile(
    r"(?:vue(?:\.global|\.min)?\.js|tdesign[^/]*\.js|request\.js)(?:[?#]|$)",
    re.IGNORECASE,
)
_GENERIC_CONTROL_RE = re.compile(
    r"<button\b|<input\b[^>]+type\s*=\s*['\"](?:checkbox|radio)['\"]",
    re.IGNORECASE,
)


def _safe_file(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError(f"插件资源路径无效: {relative}")
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"插件资源越界: {relative}")
    if not target.is_file():
        raise ValueError(f"插件资源不存在: {relative}")
    return target


def _template_path(plugin_root: Path, page: dict) -> Path:
    audience = page.get("audience") or (
        "admin" if page.get("nav_type", "admin") == "admin" else "farmer"
    )
    template = page.get("template") or f"{page['path'].rstrip('/').split('/')[-1]}.html"
    candidates = (
        [plugin_root / "templates" / "admin" / template]
        if audience == "admin" else [
            plugin_root / "templates" / "farmer" / "default" / template,
            plugin_root / "templates" / "farmer" / template,
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(f"插件页面模板不存在: {template}")


def _validate_source(path: Path, source: str) -> None:
    if path.suffix.lower() in {".html", ".css"} and _COLOR_RE.search(source):
        raise ValueError(f"插件页面禁止硬编码 CSS 色值：{path.name}")
    # TDesign 令牌是主题事实源，插件只能引用已登记的 --td-* 变量，不能声明根级令牌覆盖。
    if path.suffix.lower() == ".css" and re.search(
        r"(?:^|\})\s*:root\s*\{[^}]*--td-", source, re.IGNORECASE | re.DOTALL
    ):
        raise ValueError(f"插件页面禁止覆盖根级 --td-* 主题令牌：{path.name}")
    if _GENERIC_CONTROL_RE.search(source):
        raise ValueError(f"插件页面应使用公共 TDesign 基础控件：{path.name}")
    for resource in _RESOURCE_RE.findall(source):
        parsed = urlsplit(resource.strip())
        if parsed.scheme or parsed.netloc or resource.startswith("//"):
            raise ValueError(f"插件页面禁止加载外部资源：{path.name}")
        if _DUPLICATE_RUNTIME_RE.search(parsed.path):
            raise ValueError(f"插件页面禁止重复加载公共运行时：{path.name}")


def validate_plugin_page_sources(plugin_root: Path, pages: list[dict]) -> None:
    """校验清单页面、模板和声明的插件本地资源。"""
    plugin_root = plugin_root.resolve()
    asset_root = (plugin_root / "static").resolve()
    scanned: set[Path] = set()
    for page in pages:
        template = _template_path(plugin_root, page)
        scanned.add(template)
        for field in ("styles", "scripts"):
            for asset in page.get(field) or []:
                scanned.add(_safe_file(asset_root, asset))
    for path in scanned:
        _validate_source(path, path.read_text(encoding="utf-8"))
