# 农业政策资讯插件

本插件按固定官方来源周期抓取农业农村部和广西农业农村厅政策列表，保存标题、来源、发布日期和详情地址，并通过管理端与农户端首页挂件展示。

## 运行方式

- 自动更新周期默认 6 小时，调度器只向任务队列投递 `policy_news.refresh`。
- 每个来源独立记录最近尝试、最近成功、条数和错误信息。
- 单个来源失败时保留已有数据；两个来源均失败时任务失败但不删除旧数据。
- 只接受 `www.moa.gov.cn` 和 `nynct.gxzf.gov.cn` 的官方地址，来源地址不能通过配置修改。
- 插件禁用、卸载或升级时由框架注销挂件、任务、路由、页面和权限。

## 页面

- 管理端：`/admin/plugin/policy_news/policy_news`
- 农户端：`/farmer/plugin/policy_news/policy_news_index`

## 能力声明

插件通过 `get_routers()`、`get_pages()`、`get_task_definitions()` 和 `get_widgets()` 显式声明能力。数据库表由 `migrations/install.sql` 创建，由 `migrations/uninstall.sql` 删除；插件不直接导入其他插件或修改菜单表。
