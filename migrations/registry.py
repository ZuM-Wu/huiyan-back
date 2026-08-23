# -*- coding: utf-8 -*-
"""
迁移注册表 — 一次性迁移的统一登记与执行入口

设计说明:
- hy_schema_version(name PK, applied_at) 表记录已应用的迁移名
- _REGISTRY 为有序迁移列表，每项含 (名称, 探测函数, 执行函数)
- run_pending(): 启动时一次 SELECT 取已应用集合，对差集逐一执行并 INSERT 标记；
  已登记但目标结构缺失时执行补偿迁移
- 探测回填: 旧库若目标结构/数据已满足（历史上手工跑过孤儿脚本），
  探测函数返回 True 时直接标记已应用而不重复执行，保证纳管零风险

新增迁移约定:
- 在 _REGISTRY 末尾追加 (name, probe, apply)，name 全局唯一且不得修改
- probe(db) 返回 True 表示目标已满足；apply(db) 必须幂等
- 已通过 core/lifespan.py _run_field_migrations 接线的 7 个历史迁移
  保持原样不纳管（其自身幂等，重复纳管反而有二次执行风险）
"""
import logging

from sqlalchemy import text

from core.db.base import async_session_factory

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 基础工具
# ------------------------------------------------------------------

async def _ensure_version_table(db):
    """确保 hy_schema_version 迁移登记表存在（幂等）"""
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_schema_version ("
        "  name VARCHAR(128) NOT NULL PRIMARY KEY COMMENT '迁移名称（全局唯一）',"
        "  applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '应用时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='迁移版本登记表'"
    ))


async def _column_exists(db, table: str, column: str) -> bool:
    """检查指定表的列是否已存在（探测回填用）"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t AND COLUMN_NAME=:c"
    ), {"t": table, "c": column})
    return bool(result.scalar())


# ------------------------------------------------------------------
# 迁移项定义（原孤儿脚本纳管，探测逻辑与原脚本判断一致）
# ------------------------------------------------------------------

async def _probe_farmer_extra_fields(db) -> bool:
    """hy_farmer 扩展字段已齐备则视为已应用（以最后加的 language 列为准）"""
    return await _column_exists(db, "hy_farmer", "language")


async def _apply_farmer_extra_fields(db):
    """hy_farmer 补齐 address/remark/country/language 四个扩展列（逐列幂等）"""
    columns = [
        ("address", "ALTER TABLE hy_farmer ADD COLUMN address VARCHAR(256) DEFAULT '' COMMENT '地址'"),
        ("remark", "ALTER TABLE hy_farmer ADD COLUMN remark VARCHAR(2048) DEFAULT '' COMMENT '备注'"),
        ("country", "ALTER TABLE hy_farmer ADD COLUMN country VARCHAR(32) DEFAULT '中国' COMMENT '国家'"),
        ("language", "ALTER TABLE hy_farmer ADD COLUMN language VARCHAR(32) DEFAULT '中文简体' COMMENT '语言'"),
    ]
    for column, ddl in columns:
        if not await _column_exists(db, "hy_farmer", column):
            await db.execute(text(ddl))
            logger.info("[迁移] hy_farmer.%s 列已添加", column)


async def _probe_user_avatar_field(db) -> bool:
    """hy_farmer.avatar 列已存在则视为已应用"""
    return await _column_exists(db, "hy_farmer", "avatar")


async def _apply_user_avatar_field(db):
    """hy_farmer 补齐 avatar 头像列"""
    await db.execute(text(
        "ALTER TABLE hy_farmer ADD COLUMN avatar VARCHAR(256) DEFAULT '' COMMENT '头像URL'"
    ))
    logger.info("[迁移] hy_farmer.avatar 列已添加")


async def _probe_menu_page_type(db) -> bool:
    """hy_menu.page_type 列已存在则视为已应用"""
    return await _column_exists(db, "hy_menu", "page_type")


async def _apply_menu_page_type(db):
    """hy_menu 补齐 page_type 列并回填存量数据为 system"""
    await db.execute(text(
        "ALTER TABLE hy_menu ADD COLUMN page_type VARCHAR(32) DEFAULT 'system' "
        "COMMENT '页面类型：system/url/separator/list'"
    ))
    await db.execute(text(
        "UPDATE hy_menu SET page_type='system' WHERE page_type IS NULL OR page_type=''"
    ))
    logger.info("[迁移] hy_menu.page_type 列已添加并回填")


async def _probe_remove_non_system_menus(db) -> bool:
    """误加入的数据大屏/溯源查询菜单（id 24/25）已不存在则视为已应用"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_menu WHERE id IN (24, 25) AND nav_type='admin'"
    ))
    return result.scalar() == 0


async def _apply_remove_non_system_menus(db):
    """删除种子数据中误加入的数据大屏/溯源查询菜单记录"""
    result = await db.execute(text(
        "DELETE FROM hy_menu WHERE id IN (24, 25) AND nav_type='admin'"
    ))
    logger.info("[迁移] 已清理非系统/插件菜单 %d 条", result.rowcount)


# 有序迁移注册表：(名称, 探测函数, 执行函数)，只允许追加、禁止改名/删除
from migrations.verify_code_attempt_count import (  # noqa: E402
    probe as _probe_verify_code_attempt,
    apply as _apply_verify_code_attempt,
)
from migrations.file_log_table import (  # noqa: E402
    probe as _probe_file_log,
    apply as _apply_file_log,
)
from migrations.api_key_table import (  # noqa: E402
    probe as _probe_api_key,
    apply as _apply_api_key,
)
from migrations.api_key_plain import (  # noqa: E402
    probe as _probe_api_key_plain,
    apply as _apply_api_key_plain,
)
from migrations.ai_tables import (  # noqa: E402
    probe as _probe_ai_tables,
    apply as _apply_ai_tables,
)
from migrations.ai_message_attachments import (  # noqa: E402
    probe as _probe_ai_message_attachments,
    apply as _apply_ai_message_attachments,
)
from migrations.mcp_server_config import (  # noqa: E402
    probe as _probe_mcp_server_config,
    apply as _apply_mcp_server_config,
)
from migrations.mcp_farmer_server_table import (  # noqa: E402
    probe as _probe_mcp_farmer_table,
    apply as _apply_mcp_farmer_table,
)
from migrations.farmer_mcp_credential_table import (  # noqa: E402
    probe as _probe_farmer_mcp_credential,
    apply as _apply_farmer_mcp_credential,
)
from migrations.weather_notice_disable_auto_email import (  # noqa: E402
    probe as _probe_weather_notice_disable,
    apply as _apply_weather_notice_disable,
)
from migrations.mcp_server_test_status import (  # noqa: E402
    probe as _probe_mcp_server_test_status,
    apply as _apply_mcp_server_test_status,
)
from migrations.xiaozhi_global_key_cleanup import (  # noqa: E402
    probe as _probe_xiaozhi_global_key_cleanup,
    apply as _apply_xiaozhi_global_key_cleanup,
)
from migrations.vision_glm_llm_module import (  # noqa: E402
    probe as _probe_vision_glm_llm_module,
    apply as _apply_vision_glm_llm_module,
)
from migrations.ai_system_tools_default import (  # noqa: E402
    probe as _probe_ai_system_tools_default,
    apply as _apply_ai_system_tools_default,
)

_REGISTRY = [
    ("farmer_extra_fields", _probe_farmer_extra_fields, _apply_farmer_extra_fields),
    ("user_avatar_field", _probe_user_avatar_field, _apply_user_avatar_field),
    ("menu_page_type", _probe_menu_page_type, _apply_menu_page_type),
    ("remove_non_system_menus", _probe_remove_non_system_menus, _apply_remove_non_system_menus),
    ("verify_code_attempt_count", _probe_verify_code_attempt, _apply_verify_code_attempt),
    ("file_log_table", _probe_file_log, _apply_file_log),
    ("api_key_table", _probe_api_key, _apply_api_key),
    ("api_key_plain", _probe_api_key_plain, _apply_api_key_plain),
    ("ai_tables", _probe_ai_tables, _apply_ai_tables),
    ("ai_message_attachments", _probe_ai_message_attachments, _apply_ai_message_attachments),
    ("mcp_server_config", _probe_mcp_server_config, _apply_mcp_server_config),
    ("mcp_farmer_server_table", _probe_mcp_farmer_table, _apply_mcp_farmer_table),
    ("farmer_mcp_credential_table", _probe_farmer_mcp_credential, _apply_farmer_mcp_credential),
    ("weather_notice_disable_auto_email", _probe_weather_notice_disable, _apply_weather_notice_disable),
    ("mcp_server_test_status", _probe_mcp_server_test_status, _apply_mcp_server_test_status),
    ("xiaozhi_global_key_cleanup", _probe_xiaozhi_global_key_cleanup, _apply_xiaozhi_global_key_cleanup),
    ("vision_glm_llm_module", _probe_vision_glm_llm_module, _apply_vision_glm_llm_module),
    ("ai_system_tools_default", _probe_ai_system_tools_default, _apply_ai_system_tools_default),
]


# ------------------------------------------------------------------
# 执行入口（由 core/lifespan.py 启动流程调用）
# ------------------------------------------------------------------

async def run_pending(db=None):
    """
    执行所有未应用的注册迁移

    流程: 建登记表 → 一次查询已应用集合 → 差集逐项执行（探测满足则只标记）→ 登记

    Parameters:
        db: 可选的外部数据库会话（集成测试注入用）；
            未传入时内部创建会话（lifespan 启动路径）
    """
    if db is not None:
        await _run_pending_impl(db, commit=False)
    else:
        async with async_session_factory() as session:
            await _run_pending_impl(session, commit=True)


async def _run_pending_impl(db, *, commit: bool):
    """run_pending 的实际实现，由外层控制会话生命周期与提交"""
    await _ensure_version_table(db)
    result = await db.execute(text("SELECT name FROM hy_schema_version"))
    applied = {row[0] for row in result.all()}

    executed = 0
    for name, probe, apply in _REGISTRY:
        target_ready = await probe(db)
        if name in applied:
            if target_ready:
                continue
            # 登记存在但目标结构缺失时执行补偿，修复手工删表或上次异常中断后的状态。
            await apply(db)
            executed += 1
            logger.warning("[迁移注册表] %s 已登记但目标缺失，已执行补偿迁移", name)
            continue
        if target_ready:
            # 探测回填：旧库结构已满足，直接标记已应用
            logger.info("[迁移注册表] %s 目标已满足，标记为已应用", name)
        else:
            await apply(db)
            executed += 1
            logger.info("[迁移注册表] %s 已执行", name)
        await db.execute(text(
            "INSERT INTO hy_schema_version (name) VALUES (:name)"
        ), {"name": name})
    if commit:
        await db.commit()

    if executed:
        logger.info("[迁移注册表] 本次共执行 %d 个迁移", executed)
