"""
SQLAlchemy 异步数据库连接管理

提供异步引擎、会话工厂和 Base 声明基类。
引擎采用懒加载——首次调用 get_db() 时才建立连接。
"""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from core.config import settings
from core.time_utils import CHINA_DB_TIME_ZONE


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类 — 所有 ORM 模型的基类"""
    pass


# 异步引擎（懒加载：创建引擎不建立连接，首次使用时才 connect）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,  # 连接前 ping 检测，防止使用已断开的连接
    connect_args={
        # 每条连接显式固定中国时区，避免部署主机或 MySQL 全局配置改变业务时间。
        "init_command": f"SET time_zone = '{CHINA_DB_TIME_ZONE}'",
    },
)

# 异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_db():
    """
    获取异步数据库会话（上下文管理器用法）

    使用方式:
        async with get_db() as db:
            result = await db.execute(...)
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
