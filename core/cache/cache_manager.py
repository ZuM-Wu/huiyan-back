"""
缓存管理器
慧眼护农 3.4.12 缓存子系统

策略: 纯文件缓存（系统规范禁止引入 Redis 等外部缓存依赖）

存储格式（单文件 JSON 信封）:
    {"_expire": <过期时刻 epoch 秒, 0 表示永不过期>, "value": <缓存值>}

过期语义:
- 惰性删除：读取时发现已过期立即删除文件并返回 None
- 定期清扫：task_manager 内置 sweep_expired_cache 每日遍历删除过期文件
- 旧版裸格式（无 _expire 键的历史文件）视为已过期删除，实现自动热迁移
"""

import json
import logging
import time
from typing import Any, Optional

from core.config import BASE_DIR

logger = logging.getLogger(__name__)


class CacheManager:
    """
    缓存管理器（纯文件模式）

    预定义缓存键:
    - CACHE_PLUGIN_LIST      插件列表
    - CACHE_PLUGIN_HOOKS     插件钩子
    - CACHE_CONFIGURATION    系统配置
    - CACHE_PERMISSION       权限节点树
    - CACHE_MENU             菜单树
    """

    # 预定义缓存键
    CACHE_PLUGIN_LIST = "hy:plugin:list"
    CACHE_PLUGIN_HOOKS = "hy:plugin:hooks"
    CACHE_CONFIGURATION = "hy:config"
    CACHE_PERMISSION = "hy:permission"
    CACHE_MENU = "hy:menu"

    def __init__(self):
        # 缓存目录基于 BASE_DIR 拼接，避免随启动工作目录漂移
        self._file_cache_dir = BASE_DIR / "runtime" / "cache"
        self._file_cache_dir.mkdir(parents=True, exist_ok=True)

    async def init(self):
        """初始化缓存（文件模式无外部连接，仅打印就绪日志）"""
        logger.info(f"[CacheManager] 文件缓存已就绪: {self._file_cache_dir}")

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存（过期或格式非法时惰性删除并返回 None）"""
        return self._file_get(key)

    async def set(self, key: str, value: Any, expire: int = 3600):
        """
        设置缓存

        参数:
            expire: 过期秒数；<=0 表示永不过期（信封中 _expire 记 0）
        """
        expire_at = int(time.time()) + expire if expire > 0 else 0
        envelope = {"_expire": expire_at, "value": value}
        path = self._file_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, default=str, ensure_ascii=False)

    async def delete(self, key: str):
        """删除缓存"""
        self._file_delete(key)

    async def clear(self, pattern: str = "*"):
        """清除缓存（文件模式只清除预定义键，pattern 参数保留兼容签名）"""
        for key in [self.CACHE_PLUGIN_LIST, self.CACHE_PLUGIN_HOOKS,
                    self.CACHE_CONFIGURATION, self.CACHE_PERMISSION, self.CACHE_MENU]:
            self._file_delete(key)

    def sweep_expired(self) -> int:
        """
        遍历缓存目录删除全部已过期文件（供定时任务调用）

        返回: 本次清理的文件数
        """
        removed = 0
        now = int(time.time())
        for path in self._file_cache_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 裸格式（非信封）或已到期的信封均视为过期
                expired = (
                    not isinstance(data, dict) or "_expire" not in data
                    or (data["_expire"] != 0 and data["_expire"] < now)
                )
            except Exception as exc:
                logger.warning("[CacheManager] 缓存清扫读取失败: path=%s error=%s", path, exc)
                expired = True  # 损坏文件直接清理
            if expired:
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError as exc:
                    logger.warning("[CacheManager] 缓存清扫删除失败: path=%s error=%s", path, exc)
        return removed

    # 文件缓存辅助方法
    def _file_path(self, key: str):
        safe_key = key.replace(":", "_").replace("/", "_")
        return self._file_cache_dir / f"{safe_key}.json"

    def _file_get(self, key: str) -> Optional[Any]:
        path = self._file_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("[CacheManager] 缓存读取失败，按未命中处理: key=%s path=%s error=%s", key, path, exc)
            return None
        try:
            # 旧版裸格式（无 _expire 信封）或非法过期值均按损坏处理。
            if not isinstance(data, dict) or "_expire" not in data:
                expired = True
            else:
                expire_at = data["_expire"]
                if isinstance(expire_at, bool) or not isinstance(expire_at, (int, float)):
                    raise ValueError("缓存过期时间格式无效")
                # 信封格式：_expire=0 永不过期，否则到期惰性删除。
                expired = expire_at != 0 and expire_at < int(time.time())
        except Exception as exc:
            logger.warning("[CacheManager] 缓存内容损坏，按未命中处理: key=%s path=%s error=%s", key, path, exc)
            expired = True
        if expired:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("[CacheManager] 惰性缓存删除失败: key=%s path=%s error=%s", key, path, exc)
            return None
        return data.get("value")

    def _file_delete(self, key: str):
        path = self._file_path(key)
        if path.exists():
            path.unlink()


# 全局单例
cache_manager = CacheManager()
