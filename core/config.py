"""
慧眼护农 3.4.1 核心配置模块

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

    # 数据库配置：不内置默认凭据，须通过 .env 的 DATABASE_URL 注入（本地开发示例见部署文档）。
    DATABASE_URL: str = ""

    # JWT 密钥（Admin/Farmer 双密钥隔离）：不携带任何默认密钥，须通过 .env 注入。
    JWT_KEY_ADMIN: str = ""
    JWT_KEY_FARMER: str = ""

    # JWT 过期时间（秒）
    JWT_EXPIRE_SECONDS: int = 7200

    # 调试模式（默认 False：生产漏配 .env 时落入安全面，由 lifespan 默认密钥门禁拒启；
    # 开发环境需在 .env 中显式设置 APP_DEBUG=true）
    APP_DEBUG: bool = False

    # SQL 日志开关（默认 False：不打印 SQL 语句）
    # 独立于 APP_DEBUG，避免关闭调试模式时联动影响 JWT 密钥门禁；
    # 开发环境需查看 SQL 时在 .env 中设置 DB_ECHO=true
    DB_ECHO: bool = False

    # 应用日志配置：默认保持 stderr 输出，可选落盘到 UTF-8 滚动文件。
    LOG_FILE: str = ""
    LOG_LEVEL: str = "INFO"

    # 应用版本（用于静态资源版本号治理，模板中用 config.app_version 小写访问）
    app_version: str = "3.4.1"

    # 插件目录（相对名称，消费方须用 BASE_DIR / PLUGINS_DIR 拼绝对路径）
    PLUGINS_DIR: str = "plugins"

    # CORS 允许的来源（逗号分隔，"*" 表示全部允许）
    # 开发环境默认 "*"，生产环境应设置为具体域名，如 "https://example.com,https://admin.example.com"
    CORS_ORIGINS: str = "*"

    # 仅供停机迁移读取的旧 JJR 配置；运行期协议只读取 hardware_jjr 插件配置。
    FARMBOT_BASE_URL: str = "https://farmbot-jjr.jjr.vip"
    FARMBOT_OWNER_TOKEN: str = ""

    # MCP 服务开关（默认关闭；开发环境在 .env 中设置 MCP_ENABLED=true 开启）
    MCP_ENABLED: bool = False

    # MCP 服务挂载路径（挂载到 FastAPI 主应用下的子路径）
    MCP_MOUNT_PATH: str = "/mcp"

    # MCP 三类能力共用的账户级滑动窗口频控。
    MCP_RATE_LIMIT_REQUESTS: int = 120
    MCP_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # 锚定到项目根目录的绝对路径：避免启动工作目录不同导致 .env 加载不到
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8"
    )


# 全局单例
settings = Settings()
