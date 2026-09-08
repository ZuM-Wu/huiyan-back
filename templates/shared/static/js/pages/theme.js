/**
 * 主题设置页面脚本 — 对应路由 /admin/theme
 *
 * 后端接口（前缀 /api/admin/v1，见 request.js baseURL）:
 *   GET  /theme/list?module=site|admin|farmer   扫描该模块可用主题清单
 *   GET  /theme/current                         三端当前启用主题
 *   POST /theme/activate {module, theme}        切换启用主题（持久化 + 清缓存，免重启）
 *
 * 页面结构：顶部三 Tab（官网 / 后台管理端 / 农户端）+ 主题卡片网格
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 */
(function () {
    const { reactive, ref, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    // 三端模块配置：value（module 标识）/ label（Tab 显示名）/ count（主题数量）
    const MODULES = [
        { value: 'site',   label: '官网主题',     count: 0 },
        { value: 'admin',  label: '后台管理端主题', count: 0 },
        { value: 'farmer', label: '农户端主题',   count: 0 },
    ];

    HuiYan.createPage({
        setup() {
            // 当前激活的 Tab（默认官网）
            const activeModule = ref('site');
            // 当前 Tab 对应的主题清单
            const themes = reactive([]);
            // 当前 Tab 加载状态
            const loading = ref(false);
            // 正在启用的主题标识，格式 'module:key'，用于按钮 loading
            const activating = ref('');
            // 官网主题可见性配置（从后端读取）
            const siteThemeVisible = ref(true);
            // 官网主题启用/禁用 loading
            const togglingSiteTheme = ref(false);

            // 拉取某模块主题清单并覆盖 themes
            const loadModule = (module) => {
                loading.value = true;
                return request.get('/theme/list', { params: { module: module } })
                    .then((res) => {
                        const body = (res.data && (res.data.data || res.data)) || {};
                        const list = body.list || [];
                        // 整体替换 reactive 数组内容
                        themes.splice(0, themes.length, ...list);
                        // 更新模块计数
                        const mod = MODULES.find((m) => m.value === module);
                        if (mod) mod.count = list.length;
                    })
                    .catch(() => {
                        MessagePlugin.error('主题清单加载失败');
                        themes.splice(0, themes.length);
                    })
                    .finally(() => { loading.value = false; });
            };

            // 拉取官网主题可见性配置
            const loadSiteThemeVisible = () => {
                return request.get('/config/site_theme_visible')
                    .then((res) => {
                        const body = res.data.data || {};
                        siteThemeVisible.value = body.value !== 'false' && body.value !== false;
                        return siteThemeVisible.value;
                    });
            };

            // 切换官网主题启用/禁用
            const doToggleSiteTheme = (enable) => {
                const action = enable ? '启用' : '禁用';
                const instance = DialogPlugin.confirm({
                    header: '确认' + action + '官网主题',
                    body: enable 
                        ? '确定要启用官网主题吗？启用后访问根路径将显示官网首页。'
                        : '确定要禁用官网主题吗？禁用后访问根路径将直接跳转至农户首页。',
                    onConfirm: () => {
                        togglingSiteTheme.value = true;
                        return request.post('/config/site_theme_visible', { value: enable ? 'true' : 'false' })
                            .then(() => loadSiteThemeVisible())
                            .then((persisted) => {
                                if (persisted !== enable) {
                                    throw new Error('官网入口状态保存后校验不一致');
                                }
                                MessagePlugin.success('官网主题已' + action);
                                instance.destroy();
                            })
                            .catch((err) => {
                                const data = err && err.response && err.response.data;
                                MessagePlugin.error((data && (data.msg || data.detail)) || (err && err.message) || '操作失败');
                            })
                            .finally(() => { togglingSiteTheme.value = false; });
                    },
                    onCancel: () => { instance.destroy(); },
                    onClose: () => { instance.destroy(); }
                });
            };

            // Tab 切换：重新拉取新模块的主题清单
            const onModuleChange = (module) => {
                activeModule.value = module;
                loadModule(module);
            };

            // 启用主题（带二次确认）
            const doActivate = (module, item) => {
                const moduleLabel = (MODULES.find((m) => m.value === module) || {}).label || module;
                const instance = DialogPlugin.confirm({
                    header: '确认切换主题',
                    body: '确定将【' + moduleLabel + '】切换为「' + item.name + '」吗？切换后立即生效。',
                    onConfirm: () => {
                        activating.value = module + ':' + item.key;
                        request.post('/theme/activate', { module: module, theme: item.key })
                            .then(() => {
                                MessagePlugin.success('主题已切换为「' + item.name + '」，页面即将刷新');
                                instance.destroy();
                                // 主题切换会替换页面壳和主题自带组件，必须整页刷新才能清理旧 Vue 实例。
                                window.setTimeout(function () { window.location.reload(); }, 300);
                            })
                            .catch((err) => {
                                const detail = err && err.response && err.response.data && err.response.data.detail;
                                MessagePlugin.error(detail || '切换失败');
                            })
                            .finally(() => { activating.value = ''; });
                    }
                });
            };

            // 预览农户端主题（临时预览，不改变启用配置）
            const doPreview = (item) => {
                window.open('/farmer/home?__theme=' + encodeURIComponent(item.key), '_blank');
            };

            // 跳转到官网主题控制器独立配置页
            const goController = (item) => {
                window.location.href = '/admin/site-controller?theme=' + encodeURIComponent(item.key);
            };

            // 初始化时加载所有模块的主题计数（并行请求）
            const loadAllCounts = () => {
                MODULES.forEach((mod) => {
                    request.get('/theme/list', { params: { module: mod.value } })
                        .then((res) => {
                            const body = (res.data && (res.data.data || res.data)) || {};
                            mod.count = (body.list || []).length;
                        })
                        .catch(() => { mod.count = 0; });
                });
            };

            onMounted(() => {
                // 先加载官网主题可见性配置
                loadSiteThemeVisible()
                    .catch(() => { siteThemeVisible.value = true; })
                    .finally(() => { loadModule(activeModule.value); });
                loadAllCounts();
            });

            return {
                modules: MODULES,
                activeModule,
                themes,
                loading,
                activating,
                siteThemeVisible,
                togglingSiteTheme,
                onModuleChange,
                doToggleSiteTheme,
                doActivate,
                doPreview,
                goController,
            };
        }
    });
})();
