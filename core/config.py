"""
慧眼护农 V4 核心配置模块

使用 pydantic-settings 从 .env 文件和环境变量中加载配置，
提供 Settings 全局单例供其他模块使用。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（HuiYan_Back 的绝对路径）
# 全库所有相对路径（plugins/、runtime/ 等）统一以此为基准拼接，
# 避免因启动工作目录不同导致文件落点漂移（如在工作区根目录误建 runtime/cache）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """系统配置类 — 自动从 .env 文件和环境变量加载"""

    # 数据库配置
    DATABASE_URL: str = "mysql+aiomysql://root:123456@127.0.0.1:3306/huiyan3dot4"

    # JWT 密钥（Admin/Farmer 双密钥隔离）
    JWT_KEY_ADMIN: str = "hy_admin_jwt_secret_2026_v4"
    JWT_KEY_FARMER: str = "hy_farmer_jwt_secret_2026_v4"

    # JWT 过期时间（秒）
    JWT_EXPIRE_SECONDS: int = 7200

    # 调试模式（默认 False：生产漏配 .env 时落入安全面，由 lifespan 默认密钥门禁拒启；
    # 开发环境需在 .env 中显式设置 APP_DEBUG=true）
    APP_DEBUG: bool = False

    # SQL 日志开关（默认 False：不打印 SQL 语句）
    # 独立于 APP_DEBUG，避免关闭调试模式时联动影响 JWT 密钥门禁；
    # 开发环境需查看 SQL 时在 .env 中设置 DB_ECHO=true
    DB_ECHO: bool = False

    # 应用版本（用于静态资源版本号治理，模板中用 config.app_version 小写访问）
    app_version: str = "3.4.5"

    # 插件目录（相对名称，消费方须用 BASE_DIR / PLUGINS_DIR 拼绝对路径）
    PLUGINS_DIR: str = "plugins"

    # CORS 允许的来源（逗号分隔，"*" 表示全部允许）
    # 开发环境默认 "*"，生产环境应设置为具体域名，如 "https://example.com,https://admin.example.com"
    CORS_ORIGINS: str = "*"

    # MCP 服务开关（默认关闭；开发环境在 .env 中设置 MCP_ENABLED=true 开启）
    MCP_ENABLED: bool = False

    # MCP 服务挂载路径（挂载到 FastAPI 主应用下的子路径）
    MCP_MOUNT_PATH: str = "/mcp"

    # 锚定到项目根目录的绝对路径：避免启动工作目录不同导致 .env 加载不到
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8"
    )


# 全局单例
settings = Settings()
