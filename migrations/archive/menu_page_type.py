"""
菜单页面类型迁移脚本 - 添加 page_type 字段
"""

from sqlalchemy import text


async def migrate(db):
    """
    1. 为 hy_menu 表新增 page_type 字段
    2. 现有记录默认设为 'system'
    3. 前台种子数据补全分类
    """
    
    # 1. 检查字段是否已存在
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_menu' AND COLUMN_NAME='page_type'"
    ))
    exists = result.scalar()
    
    if not exists:
        # 2. 新增字段（MySQL 8.0 不支持 IF NOT EXISTS）
        await db.execute(text(
            "ALTER TABLE hy_menu ADD COLUMN page_type VARCHAR(32) DEFAULT 'system' COMMENT '页面类型：system/url/separator/list'"
        ))
        print("[DB 迁移] 菜单 page_type 字段已添加")
    else:
        print("[DB 迁移] 菜单 page_type 字段已存在，跳过")
    
    # 3. 填充现有数据
    await db.execute(text(
        "UPDATE hy_menu SET page_type='system' WHERE page_type IS NULL OR page_type = ''"
    ))


async def rollback(db):
    """回滚脚本"""
    await db.execute(text(
        "ALTER TABLE hy_menu DROP COLUMN IF EXISTS page_type"
    ))
    print("[DB 回滚] 菜单 page_type 字段已删除")
