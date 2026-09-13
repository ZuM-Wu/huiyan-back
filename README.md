# 慧眼护农智慧农业业务系统

> 这是本人的**首个 Vibe Coding 项目**，技术有限，真诚邀请各位一起协助，让这套系统越来越完善。

慧眼护农是一套**模块化**的智慧农业管理系统，当前版本 `3.4.0`，是**前后端一体的完整项目**：后端基于 FastAPI + 异步 SQLAlchemy + MySQL 8 提供 API 与插件框架，前台覆盖管理端、农户端与站点三端页面；业务能力以可卸载插件（模块）方式组织，并集成 AgentScope AI 对话、Ultralytics 智能识别与系统 MCP。

> 本项目用于学习、教学与非商业用途，开源协议见 [LICENSE](LICENSE)。禁止任何形式的商业使用，二次开发须保留原作者署名。

## 功能特性（模块化）

系统以可卸载插件（模块）方式组织业务能力，核心平台与各类插件模块如下：

**核心平台**

- 认证与权限：Admin / 农户双体系（农户端未完善，正在努力完善中），RBAC 权限节点，JWT 双密钥隔离，验证码登录。
- 插件框架：插件扫描装配、启用停用、运行时升级、插件数据库体检。
- 任务调度：APScheduler 周期任务 + 任务队列 Worker。
- 事件与管道：可靠事件 Outbox、Pipeline 扩展点。

**业务插件（Addon）**

- 硬件管理与物联网：慧眼 / JJR 设备，协议来源接入、设备列表、地块绑定、地图标记、实时与历史数据。
- 智能识别：YOLO（Ultralytics）模型管理、地块绑定、置信度控制、识别记录与分析。
- 知识与政策：知识库检索、政策资讯订阅。
- 应用管理：应用与 APK 包管理。
- 文件下载：文件共享与下载。
- 消息推送：站内推送、企业微信机器人、管理员通知。

**AI 与 MCP**

- AI 对话：AgentScope 2.0.7 运行时，多模型、会话、Skill 与 MCP 装配。
- 大模型网关：DeepSeek、GLM 视觉等模型插件。
- 小智连接桥：WSS 连接、工具缓存与自定义 MCP。
- 系统 MCP：只读 Tools / Resources / Prompts，账户级 API Key 鉴权与频控。

**存储与外部服务**

- 对象存储：本地 / 七牛云，统一存储调度、私密签名、文件迁移。
- 短信：IDC 短信发送。
- 邮件：SMTP 邮件与模板。
- 天气：高德 / 和风天气拉取与订阅。
- 三方认证：芝麻信用认证。

## 技术栈

**后端**

- Python 3.12
- FastAPI + Uvicorn（单 worker）
- SQLAlchemy（异步）+ aiomysql + MySQL 8
- pydantic-settings（环境配置）
- python-jose + bcrypt / passlib（JWT 与密码哈希）
- FastMCP + WebSocket（系统 MCP / 小智连接）
- AgentScope 2.0.7（AI 运行时）
- Ultralytics 8（目标检测）
- APScheduler（任务调度）
- Jinja2（模板渲染）
- httpx / qrcode / Qiniu SDK（外部服务集成）

**前端**

- 三端页面：管理端 / 农户端 / 站点
- TDesign Vue Next（统一组件库）
- 原生 JavaScript（SPA 导航、组件与仪表盘挂件）

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
