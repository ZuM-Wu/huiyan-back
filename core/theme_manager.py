"""
主题管理器

统一负责「模块 / 主题」的发现、解析、回退与 Jinja2 环境构建。

目录约定:
    templates/{module}/{theme}/          模块主题目录（module ∈ {site, admin, farmer}）
        theme.json                       主题清单（发现 + 校验 + 展示依据）
        base.html                        主题骨架（缺失则判定主题无效）
        assets/                          主题专属静态资源（挂载 /theme/{module}/{theme}）

主题解析优先级（由调用方传入 preview 预览值）:
    1. 预览参数（仅管理员，临时预览）
    2. 系统配置 {module}_theme（管理员在主题设置页设定）
    3. 兜底 default

回退策略: 采用 Jinja2 ChoiceLoader 做「模板级回退」，当前主题缺某个
模板时自动落到 default 主题的同名文件；主题只需覆盖差异页面。
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from jinja2 import BaseLoader, Environment, FileSystemLoader, ChoiceLoader, PrefixLoader

from core.config import settings
# 插件模块目录名统一从插件管理器导入（单一数据源，避免两处维护不同步，收集插件模板时遍历）
from core.plugin_manager import PLUGIN_MODULES

logger = logging.getLogger(__name__)

# 支持主题化的模块（site=官网、admin=后台管理端、farmer=农户端）
THEME_MODULES = ("site", "admin", "farmer")
# 各模块在后台「主题设置」页显示的中文名
THEME_MODULE_LABELS = {
    "site":   "官网主题",
    "admin":  "后台管理端主题",
    "farmer": "农户端主题",
}
# 兜底主题标识（始终视为可用）
DEFAULT_THEME = "default"


class ThemeManager:
    """主题管理器（全局单例）"""

    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.templates_root = self.base_dir / "templates"
        self.plugins_dir = self.base_dir / "plugins"
        # 各模块当前启用主题（启动时从配置载入，主题设置页切换时更新）
        self._active = {module: DEFAULT_THEME for module in THEME_MODULES}
        # Jinja2 环境缓存：key=(module, theme)
        self._envs = {}

    # ------------------------------------------------------------------
    # 活动主题（内存缓存，避免每次页面请求查库）
    # ------------------------------------------------------------------
    async def load_active(self, db) -> None:
        """启动时从系统配置载入各模块当前启用主题"""
        from core.config_manager import ConfigManager
        cm = ConfigManager()
        for module in THEME_MODULES:
            value = await cm.get(f"{module}_theme", db)
            self._active[module] = self.resolve(module, value or DEFAULT_THEME)
        logger.info("[主题] 当前启用: %s", self._active)

    def get_active(self, module: str) -> str:
        """获取模块当前启用主题（已解析、保证可用）"""
        return self._active.get(module, DEFAULT_THEME)

    def set_active(self, module: str, theme: str) -> str:
        """
        设置模块当前启用主题（内存缓存 + 清对应模块的 env 缓存）。
        返回实际生效的主题（经回退校验）。
        """
        resolved = self.resolve(module, theme)
        self._active[module] = resolved
        # 清除该模块所有已缓存 env，下次请求重建
        for key in [k for k in self._envs if k[0] == module]:
            self._envs.pop(key, None)
        return resolved

    # ------------------------------------------------------------------
    # 主题发现与校验
    # ------------------------------------------------------------------
    def _module_root(self, module: str) -> Path:
        return self.templates_root / module

    def theme_dir(self, module: str, theme: str) -> Path:
        return self._module_root(module) / theme

    def theme_exists(self, module: str, theme: str) -> bool:
        """校验主题是否可用：目录存在 + 含 base.html + theme.json 合法"""
        if not theme:
            return False
        d = self.theme_dir(module, theme)
        if not d.is_dir() or not (d / "base.html").exists():
            return False
        return self._read_manifest(module, theme) is not None

    def resolve(self, module: str, theme: Optional[str]) -> str:
        """解析主题：可用则返回自身，否则回退 default"""
        if theme and self.theme_exists(module, theme):
            return theme
        return DEFAULT_THEME

    def _read_manifest(self, module: str, theme: str) -> Optional[dict]:
        """读取并校验 theme.json；非法返回 None"""
        manifest = self.theme_dir(module, theme) / "theme.json"
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[主题] 解析 %s/%s/theme.json 失败: %s", module, theme, e)
            return None
        if not isinstance(data, dict):
            logger.warning("[主题] %s/%s/theme.json 顶层必须是对象", module, theme)
            return None
        # 兼容旧版 key/module，同时归一化 Standard 要求的稳定字段。
        data.setdefault("theme_id", data.get("key") or theme)
        data.setdefault("surface", data.get("module") or module)
        data.setdefault("owner", f"theme:{module}:{theme}")
        data.setdefault("enabled", True)
        data.setdefault("template_overrides", [])
        data.setdefault("static_assets", [])
        data.setdefault("component_tokens", {})
        data.setdefault("dependencies", [])
        data.setdefault("conflicts", [])
        data.setdefault("fallback_theme", DEFAULT_THEME)
        data.setdefault("compatible_app_versions", [data.get("min_app_version", "")])
        if not isinstance(data.get("template_overrides"), list):
            data["template_overrides"] = []
        if not isinstance(data.get("static_assets"), list):
            data["static_assets"] = []
        required_fields = ("theme_id", "surface", "name", "version", "author", "description")
        if any(not isinstance(data.get(field), str) or not data[field].strip()
               for field in required_fields):
            logger.warning("[主题] %s/%s/theme.json 缺少规范字段", module, theme)
            return None
        # 校验：key/module 存在且与目录/模块不一致时拒绝
        if data.get("theme_id") != theme or (data.get("key") and data.get("key") != theme):
            logger.warning("[主题] %s/%s 的 theme.json key 与目录名不一致，跳过", module, theme)
            return None
        if data.get("surface") != module or (data.get("module") and data.get("module") != module):
            logger.warning("[主题] %s/%s 的 theme.json module 声明不符，跳过", module, theme)
            return None
        return data

    def list_themes(self, module: str) -> List[dict]:
        """
        扫描模块下所有合法主题，返回清单。
        每项含 manifest 字段 + is_active + preview_url。
        """
        root = self._module_root(module)
        themes: List[dict] = []
        if not root.is_dir():
            return themes
        active = self.get_active(module)
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest = self._read_manifest(module, child.name)
            if manifest is None:
                continue
            preview = manifest.get("preview") or ""
            # 主题专属资源统一位于 assets/，静态挂载点也以该目录为根。
            preview_path = child / "assets" / preview if preview else None
            themes.append({
                "key": child.name,
                "name": manifest.get("name") or child.name,
                "version": manifest.get("version") or "",
                "author": manifest.get("author") or "",
                "description": manifest.get("description") or "",
                "preview_url": (
                    f"/theme/{module}/{child.name}/{preview}"
                    if preview_path and preview_path.is_file() else ""
                ),
                "is_active": child.name == active,
            })
        return themes

    # ------------------------------------------------------------------
    # Jinja2 环境构建（按 模块+主题 缓存）
    # ------------------------------------------------------------------
    def get_env(self, module: str, preview: Optional[str] = None) -> Environment:
        """
        获取模块 Jinja2 环境（惰性构建并缓存）。

        参数:
            module:  模块标识（site / admin / farmer）
            preview: 预览主题标识（仅临时预览用）。传入后按解析优先级
                     临时使用该主题渲染，非法值自动回退 default；
                     不影响系统配置与当前启用主题。
        """
        if preview:
            theme = self.resolve(module, preview)
        else:
            theme = self.get_active(module)
        key = (module, theme)
        if key not in self._envs:
            self._envs[key] = self._build_env(module, theme)
        return self._envs[key]

    def _build_env(self, module: str, theme: str) -> Environment:
        """构建 ChoiceLoader 环境：当前主题 -> default -> 插件模板目录"""
        search_dirs: List[str] = []

        # 1. 当前主题目录（非 default 时优先）
        if theme != DEFAULT_THEME:
            td = self.theme_dir(module, theme)
            if td.is_dir():
                search_dirs.append(str(td))
        # 2. default 主题目录（兜底）
        dd = self.theme_dir(module, DEFAULT_THEME)
        if dd.is_dir():
            search_dirs.append(str(dd))

        plugin_loaders = self._plugin_template_loaders(module, theme)
        loaders: List[BaseLoader] = [FileSystemLoader(d) for d in search_dirs]
        if plugin_loaders:
            loaders.append(PrefixLoader({"plugins": PrefixLoader(plugin_loaders)}))

        env = Environment(
            loader=ChoiceLoader(loaders),
            autoescape=True,
        )
        env.globals["config"] = settings
        env.globals["module"] = module
        env.globals["theme"] = theme
        env.globals["theme_static"] = f"/theme/{module}/{theme}"
        logger.info("[主题] 构建环境 %s/%s（%d 个搜索目录）", module, theme, len(search_dirs))
        return env

    def _plugin_dir_candidates(self, plugin_dir: Path, module: str, theme: str) -> List[str]:
        """收集单个插件的模板目录候选（_plugin_template_dirs 循环体辅助）"""
        dirs: List[str] = []
        if module == "admin":
            tpl = plugin_dir / "templates" / "admin"
            if tpl.is_dir():
                dirs.append(str(tpl))
            return dirs
        base = plugin_dir / "templates" / "farmer"
        if theme != DEFAULT_THEME and (base / theme).is_dir():
            dirs.append(str(base / theme))
        if (base / DEFAULT_THEME).is_dir():
            dirs.append(str(base / DEFAULT_THEME))
        return dirs

    def _plugin_template_loaders(self, module: str, theme: str) -> dict[str, BaseLoader]:
        """按插件名构建独立 Loader，禁止插件模板进入扁平搜索空间。"""
        loaders: dict[str, BaseLoader] = {}
        if not self.plugins_dir.is_dir():
            return loaders
        for module_name in PLUGIN_MODULES:
            module_path = self.plugins_dir / module_name
            if not module_path.is_dir():
                continue
            for plugin_dir in module_path.iterdir():
                if not plugin_dir.is_dir() or plugin_dir.name.startswith(("_", ".")):
                    continue
                dirs = self._plugin_dir_candidates(plugin_dir, module, theme)
                if dirs:
                    loaders[plugin_dir.name] = ChoiceLoader([
                        FileSystemLoader(path) for path in dirs
                    ])
        return loaders

    def mount_theme_static(self, app) -> None:
        """为各模块所有已发现主题挂载专属静态资源目录 /theme/{module}/{theme}"""
        from core.static_files import CachedStaticFiles
        for module in THEME_MODULES:
            for theme in self.list_themes(module):
                assets = self.theme_dir(module, theme["key"]) / "assets"
                if assets.is_dir():
                    app.mount(
                        f"/theme/{module}/{theme['key']}",
                        CachedStaticFiles(directory=str(assets)),
                        name=f"theme_{module}_{theme['key']}",
                    )
                    logger.info("[主题] 挂载静态: /theme/%s/%s", module, theme["key"])

    def mount_plugin_static(self, app) -> None:
        """把插件自有资源挂到稳定命名空间 /plugin-assets/{plugin}/。"""
        from core.static_files import CachedStaticFiles
        if not self.plugins_dir.is_dir():
            return
        for module_name in PLUGIN_MODULES:
            module_dir = self.plugins_dir / module_name
            if not module_dir.is_dir():
                continue
            for plugin_dir in module_dir.iterdir():
                assets = plugin_dir / "static"
                if not assets.is_dir():
                    continue
                app.mount(
                    f"/plugin-assets/{plugin_dir.name}",
                    CachedStaticFiles(directory=str(assets)),
                    name=f"plugin_assets_{plugin_dir.name}",
                )


# 全局单例
theme_manager = ThemeManager()
