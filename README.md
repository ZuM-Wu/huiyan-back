# 慧眼护农智慧农业业务系统（后端）

慧眼护农是一套面向智慧农业场景的业务系统后端，当前版本 `3.4.0`。项目一体化承载 API、前端静态资源、插件体系与 AI 能力，采用可卸载业务优先放插件的架构，运行于 FastAPI + 异步 SQLAlchemy + MySQL 8。

> 本项目用于学习、教学与非商业用途，开源协议见 [LICENSE](LICENSE)。禁止任何形式的商业使用，二次开发须保留原作者署名。

## 功能特性

- 硬件管理与物联网：统一接入协议来源、设备列表、地块绑定、地图标记、实时/历史数据与详情抽屉。
- 智能识别：基于 YOLO（Ultralytics）的模型管理、地块绑定、置信度控制与识别记录。
- AI 对话：AgentScope 2.0.7 作为 AI 运行时，支持多模型、会话、MCP 工具与 Skill 装配。
- 系统 MCP：对外提供只读 Tools / Resources / Prompts，账户级 API Key 鉴权与频控。
- 对象存储与上传：本地与七牛云对象存储插件，统一存储调度与文件迁移。
- 插件体系：Addon / 认证 / 邮件 / 天气 / OSS / 短信等多类插件，支持运行时启用、停用与升级。
- 认证与权限：Admin / 农户双体系，RBAC 权限节点与 JWT 双密钥隔离。

## 技术栈

- Python 3.12
- FastAPI + Uvicorn（单 worker）
- SQLAlchemy（异步）+ aiomysql + MySQL 8
- pydantic-settings（环境配置）
- AgentScope 2.0.7（AI 运行时）
- Ultralytics 8（目标检测）
- APScheduler（任务调度）

## 目录结构

```
HuiYan_Back
├── api/                 # 管理端与农户端 API 路由
├── core/                # 核心框架：配置、插件管理、数据库、生命周期、存储等
├── plugins/             # 可卸载业务插件（addon / certification / mail / weather / oss 等）
├── services/            # 服务层：任务调度、MCP、对象存储、AgentScope 等
├── schemas/             # 请求/响应模型
├── migrations/          # 数据库迁移与归档
├── control_center/      # 本机开发控制中心
├── static/              # 静态资源入口
├── templates/           # 三端模板（管理端 / 农户端 / 站点）
├── main.py              # 主入口
├── requirements.txt     # 运行时依赖
└── CHANGELOG.md         # 后端更新日志
```

## 快速开始

### 环境要求

- Python >= 3.12
- MySQL 8（默认本地 `127.0.0.1:3306`）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置环境变量

复制项目根目录的 `.env` 文件（本仓库不携带任何真实凭据与密钥），按需填写：

| 变量名 | 说明 |
| --- | --- |
| `DATABASE_URL` | 数据库连接串，例如 `mysql+aiomysql://<用户>:<密码>@127.0.0.1:3306/<库名>` |
| `JWT_KEY_ADMIN` | 管理端 JWT 签名密钥（请使用随机长字符串） |
| `JWT_KEY_FARMER` | 农户端 JWT 签名密钥（请与管理员密钥不同） |
| `APP_DEBUG` | 调试开关，开发环境设为 `true` |
| `DB_ECHO` | SQL 日志开关，默认 `false` |
| `MCP_ENABLED` | 是否启用系统 MCP，开发环境 `true` |

### 启动

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

## 部署约束

- 仅支持单 worker 部署，严禁使用 `--workers > 1`。系统内的防重复提交缓存、频控计数、插件禁用集合等均为进程内内存状态，多 worker 会导致状态漂移与安全机制失效。
- 生产环境须在 `.env` 中配置独立的 `DATABASE_URL`、`JWT_KEY_ADMIN`、`JWT_KEY_FARMER`，并将 `APP_DEBUG` 设为 `false`。未配置 JWT 密钥时，生产模式将拒绝启动。

## 开源协议

本项目采用自定义非商业学习许可协议，详见 [LICENSE](LICENSE)：

- 允许免费复制、修改、二次开发，用于学习、教学、科研等非商业目的；
- 禁止一切直接或间接的商业用途；
- 复制、修改、二次开发、再分发时必须在显著位置保留原作者的版权声明与署名。

## 版权署名

版权所有 © 2026 ZuM-Wu (ZM Wu)