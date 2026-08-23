"""
清理非系统/插件菜单迁移脚本
删除种子数据中误加入的 "数据大屏" 和 "溯源查询" 菜单记录。
"""

from sqlalchemy import text


async def migrate(db):
    """
    删除后台菜单中 id 为 24、25 的记录。
    这两个菜单既不是系统页面也不是插件页面，不应出现在后台导航中。
    """
    result = await db.execute(text(
        "DELETE FROM hy_menu WHERE id IN (24, 25) AND nav_type = 'admin'"
    ))
    print(f"[DB 迁移] 已清理非系统/插件菜单 {result.rowcount} 条")


async def rollback(db):
    """回滚脚本：重新插入被删除的菜单记录（仅用于测试回滚）。"""
    await db.execute(text(
        """
        INSERT INTO hy_menu
            (id, name, title, path, icon, parent_id, sort_order, plugin, visible, nav_type, page_type)
        VALUES
            (24, 'data', '数据大屏', '/admin/data', 'chart-bar', 0, 6, '', 0, 'admin', 'system'),
            (25, 'trace', '溯源查询', '/admin/trace', 'search', 2, 3, '', 0, 'admin', 'system')
        ON DUPLICATE KEY UPDATE
            name=VALUES(name), title=VALUES(title), path=VALUES(path), icon=VALUES(icon),
            parent_id=VALUES(parent_id), sort_order=VALUES(sort_order), plugin=VALUES(plugin),
            visible=VALUES(visible), nav_type=VALUES(nav_type), page_type=VALUES(page_type)
        """
    ))
    print("[DB 回滚] 非系统/插件菜单已恢复")
