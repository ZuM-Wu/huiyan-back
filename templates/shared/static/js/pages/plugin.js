/**
 * 插件管理页面脚本
 * 已安装插件与可安装插件（扫描 plugins/ 目录发现、含已卸载但文件仍保留的插件）合并展示于同一表格，
 * 已安装的按安装顺序排在前面，未安装的排在后面。
 */
(function () {
    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            const tableData = ref([]);
            const loading = ref(false);
            const searchKeyword = ref('');
            const statusFilter = ref('');
            const installingName = ref('');
            const upgradingName = ref('');
            const syncVisible = ref(false);
            const syncLoading = ref(false);
            const syncRows = ref([]);
            const configVisible = ref(false);
            const configLoading = ref(false);
            const configSaving = ref(false);
            const configPlugin = ref(null);
            const configSchema = ref([]);
            const configForm = ref({});
            const uploadPolicies = ref([]);
            const controlCenterAvailable = ref(false);
            const pagination = reactive({ current: 1, pageSize: 15, total: 0 });

            const columns = [
                { colKey: 'id', title: 'ID', width: 70, ellipsis: true },
                { colKey: 'title', title: '应用名称', width: 220, ellipsis: true },
                { colKey: 'module_label', title: '类别', width: 100 },
                { colKey: 'author', title: '开发者', width: 140, ellipsis: true },
                { colKey: 'version', title: '版本', width: 100 },
                { colKey: 'status', title: '状态', width: 100, cell: 'status' },
                { colKey: 'actions', title: '操作', width: 310, cell: 'actions' }
            ];
            const syncColumns = [
                { colKey: 'title', title: '插件名称', minWidth: 160, ellipsis: true },
                { colKey: 'module_label', title: '类别', width: 90 },
                { colKey: 'installed_version', title: '安装版本', width: 110, cell: 'installed_version' },
                { colKey: 'version', title: '目录版本', width: 110, cell: 'version' },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'version_state', title: '版本状态', width: 110, cell: 'version_state' },
                { colKey: 'sync_actions', title: '操作', width: 150, cell: 'sync_actions' }
            ];

            const probeControlCenter = () => {
                const controller = new AbortController();
                const timer = window.setTimeout(() => controller.abort(), 1200);
                return fetch('http://localhost:8765/api/ping', {
                    mode: 'cors', cache: 'no-store', signal: controller.signal
                }).then((response) => response.ok ? response.json() : null).then((data) => {
                    controlCenterAvailable.value = Boolean(data && data.protocol_version === 1);
                    return controlCenterAvailable.value;
                }).catch(() => {
                    controlCenterAvailable.value = false;
                    return false;
                }).finally(() => window.clearTimeout(timer));
            };

            const openControlCenter = async (row) => {
                const available = await probeControlCenter();
                if (!available) {
                    MessagePlugin.warning('请先双击桌面的“慧眼本地控制中心”，再应用更新');
                    return;
                }
                const operationId = row.pending_update?.operation_id || row.operation_id || '';
                const target = 'http://localhost:8765/?operation_id=' + encodeURIComponent(operationId);
                window.location.assign(target);
            };

            // 合并后的全量数据（已安装 + 未安装），用于本地搜索与分页
            let allData = [];

            // 根据搜索关键字、状态筛选与分页参数刷新表格展示内容
            const applyView = () => {
                const keyword = (searchKeyword.value || '').trim().toLowerCase();
                const status = statusFilter.value;
                let filtered = allData;
                // 关键字过滤
                if (keyword) {
                    filtered = filtered.filter((row) =>
                        (row.name || '').toLowerCase().includes(keyword)
                        || (row.title || '').toLowerCase().includes(keyword)
                        || (row.author || '').toLowerCase().includes(keyword)
                    );
                }
                // 状态过滤
                if (status) {
                    if (status === 'uninstalled') {
                        filtered = filtered.filter((row) => row.installed === false);
                    } else {
                        const statusVal = parseInt(status, 10);
                        filtered = filtered.filter((row) => row.installed !== false && row.status === statusVal);
                    }
                }
                pagination.total = filtered.length;
                const start = (pagination.current - 1) * pagination.pageSize;
                tableData.value = filtered.slice(start, start + pagination.pageSize);
            };

            // 应用管理只读取后端定义的应用范围；短信、邮件、天气、存储等
            // 独立管理模块由各自设置页维护，不在这里重复展示。
            const fetchData = () => {
                loading.value = true;
                Promise.all([
                    request.get('/plugin/list'),
                    request.get('/plugin/discover')
                ]).then(([listRes, discoverRes]) => {
                    const listData = listRes.data.data || listRes.data;
                    const discoverData = discoverRes.data.data || discoverRes.data;
                    const discovered = discoverData.list || [];
                    const discoveredByName = new Map(discovered.map((item) => [item.name, item]));
                    const installed = (listData.list || listData.items || []).map((item) => {
                        const disk = discoveredByName.get(item.name) || {};
                        return {
                            ...item,
                            installed: true,
                            installed_version: item.version,
                            available_version: disk.version || item.version,
                            upgrade_available: disk.upgrade_available === true,
                            version_state: disk.version_state || 'current',
                            pending_update: disk.pending_update || null
                        };
                    });
                    // 未安装的插件（含已卸载但文件仍保留、以及从未安装过的插件）
                    const uninstalled = discovered
                        .filter((item) => !item.installed)
                        .map((item) => ({
                            id: null,
                            name: item.name,
                            title: item.title,
                            version: item.version,
                            author: item.author || '',
                            module: item.module,
                            module_label: item.module_label || item.module,
                            status: null,
                            install_time: null,
                            installed: false
                        }));
                    // 已安装在前（按安装顺序），未安装在后
                    allData = installed.concat(uninstalled);
                    pagination.current = 1;
                    applyView();
                }).catch(() => {
                    MessagePlugin.error('获取插件列表失败');
                }).finally(() => {
                    loading.value = false;
                    probeControlCenter();
                });
            };

            const onPageChange = (pageInfo) => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                applyView();
            };

            // 重置筛选条件
            const resetFilter = () => {
                searchKeyword.value = '';
                statusFilter.value = '';
                pagination.current = 1;
                applyView();
            };

            // 启用/停用插件
            const togglePlugin = (row, status) => {
                const action = status === 1 ? 'enable' : 'disable';
                request.post('/plugin/' + action + '/' + row.name).then(() => {
                    MessagePlugin.success(status === 1 ? '已启用' : '已停用');
                    fetchData();
                }).catch(() => { MessagePlugin.error('操作失败'); });
            };

            // 插件安装会动态注册权限节点。立即刷新当前会话权限缓存，
            // 避免管理员首次进入新插件页面时仍使用安装前的权限列表。
            const refreshAuthCache = () => request.get('/permission/my-auth').then((res) => {
                const data = (res.data && res.data.data) || res.data || {};
                localStorage.setItem('admin_auth', JSON.stringify(data.auth || []));
                localStorage.setItem('admin_pages', JSON.stringify(data.pages || {}));
            });

            // 卸载插件（二次确认）
            const confirmUninstall = (row) => {
                const instance = DialogPlugin.confirm({
                    header: '确认卸载',
                    body: '确定要卸载插件 [' + row.title + '] 吗？卸载后插件文件仍会保留，可在列表中重新安装。',
                    onConfirm: () => {
                        request.post('/plugin/uninstall/' + row.name).then(() => {
                            MessagePlugin.success('已卸载');
                            instance.destroy();
                            // 清除侧边栏缓存并强制刷新，确保卸载后菜单立即消失
                            localStorage.removeItem('admin_menus');
                            localStorage.removeItem('admin_auth');
                            localStorage.removeItem('admin_pages');
                            window.location.reload();
                        }).catch(() => { MessagePlugin.error('卸载失败'); });
                    }
                });
            };

            // 安装未安装的插件
            const doInstall = (row) => {
                installingName.value = row.name;
                request.post('/plugin/install/' + row.name).then(async (res) => {
                    MessagePlugin.success(res.data.msg || '安装成功');
                    // 清除侧边栏缓存，确保新插件菜单立即可见
                    localStorage.removeItem('admin_menus');
                    localStorage.removeItem('admin_auth');
                    localStorage.removeItem('admin_pages');
                    await refreshAuthCache();
                    fetchData();
                }).catch((err) => {
                    MessagePlugin.error(err.response?.data?.detail || '安装失败');
                }).finally(() => { installingName.value = ''; });
            };

            const doUpgrade = (row) => {
                upgradingName.value = row.name;
                request.post('/plugin/upgrade/' + row.name).then((res) => {
                    MessagePlugin.success(res.data.msg || '更新计划已确认，重启后生效');
                    fetchData();
                }).catch(() => {
                    // 业务错误由 request.js 展示，保留当前列表供管理员重试。
                }).finally(() => { upgradingName.value = ''; });
            };

            const versionStateLabel = (state) => ({
                current: '已是最新',
                update_available: '可更新',
                awaiting_restart: '待重启生效',
                not_installed: '未安装',
                local_newer: '安装版较新',
                invalid: '版本异常'
            }[state] || '版本异常');

            const versionStateTheme = (state) => {
                if (state === 'update_available') return 'primary';
                if (state === 'awaiting_restart') return 'warning';
                if (state === 'invalid' || state === 'local_newer') return 'warning';
                return 'default';
            };

            const scanAllPlugins = () => {
                syncLoading.value = true;
                return request.get('/plugin/discover').then((res) => {
                    const data = (res.data && res.data.data) || res.data || {};
                    syncRows.value = data.list || [];
                }).catch(() => {
                    MessagePlugin.error('同步插件目录失败');
                }).finally(() => { syncLoading.value = false; });
            };

            const openSyncDialog = () => {
                syncVisible.value = true;
                scanAllPlugins();
            };

            const upgradeFromSync = (row) => {
                upgradingName.value = row.name;
                request.post('/plugin/upgrade/' + row.name).then((res) => {
                    MessagePlugin.success(res.data.msg || '更新计划已确认，重启后生效');
                    return Promise.all([scanAllPlugins(), fetchData()]);
                }).catch(() => {
                    // 统一请求层已显示升级失败的业务错误。
                }).finally(() => { upgradingName.value = ''; });
            };

            const openConfig = (row) => {
                configPlugin.value = row;
                configVisible.value = true;
                configLoading.value = true;
                request.get('/plugin/config/' + row.name).then((res) => {
                    const data = (res.data && res.data.data) || res.data || {};
                    configSchema.value = data.schema || [];
                    configForm.value = { ...(data.current || {}) };
                    uploadPolicies.value = data.upload_policies || [];
                }).catch(() => {
                    MessagePlugin.error('获取插件配置失败');
                    configVisible.value = false;
                }).finally(() => { configLoading.value = false; });
            };

            const saveConfig = () => {
                if (!configPlugin.value) return;
                configSaving.value = true;
                request.put('/plugin/config/' + configPlugin.value.name, {
                    config: configForm.value
                }).then(() => {
                    MessagePlugin.success('插件配置已保存');
                    configVisible.value = false;
                }).catch(() => {}).finally(() => { configSaving.value = false; });
            };

            const goUploadSettings = () => {
                configVisible.value = false;
                HuiYan.loadPage('/admin/system?tab=upload');
            };

            onMounted(() => fetchData());

            return {
                tableData, loading, searchKeyword, statusFilter, pagination, columns,
                installingName, upgradingName,
                syncVisible, syncLoading, syncRows, syncColumns,
                configVisible, configLoading, configSaving, configPlugin,
                configSchema, configForm, uploadPolicies, controlCenterAvailable,
                fetchData, applyView, resetFilter, onPageChange, togglePlugin, confirmUninstall,
                doInstall, doUpgrade,
                openSyncDialog, upgradeFromSync, versionStateLabel, versionStateTheme,
                openConfig, saveConfig, goUploadSettings, openControlCenter
            };
        }
    });
})();
