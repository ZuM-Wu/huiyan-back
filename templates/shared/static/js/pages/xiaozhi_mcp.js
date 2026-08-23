/** 小智 AI MCP 管理页：连接监控、独立外部 MCP 配置和缓存工具预览。 */
(function () {
    const { computed, onBeforeUnmount, onMounted, reactive, ref } = Vue;
    const { DialogPlugin, MessagePlugin } = TDesign;

    const mountPage = () => HuiYan.createPluginPage({ plugin: 'xiaozhi_mcp', page: 'xiaozhi_mcp',
        setup() {
            const query = new URLSearchParams(window.location.search);
            const requestedTab = query.get('tab') === 'runtime' ? 'config' : query.get('tab');
            const activeTab = ref(['config', 'mcp', 'tools'].includes(requestedTab) ? requestedTab : 'config');
            const config = reactive({ endpoint_url: '', endpoint_configured: false });
            const form = reactive({ enabled: 0, endpoint_url: '' });
            const status = reactive({ status: 'stopped', reconnect_attempts: 0, last_connected_at: '', last_disconnected_reason: '', last_tool_count: 0 });
            const tools = reactive({ list: [], source_stats: { core: 0, plugin: 0, external: 0 }, estimated_tokens: 0, capacity_limit: 0, over_limit: false });
            const externalServers = ref([]);
            const configLoading = ref(false);
            const statusLoading = ref(false);
            const toolsLoading = ref(false);
            const toolsError = ref('');
            const serversLoading = ref(false);
            const saving = ref(false);
            const testing = ref(false);
            const operating = ref(false);
            const serverVisible = ref(false);
            const serverSaving = ref(false);
            const serverFormRef = ref(null);
            const serverForm = reactive({ id: 0, name: '', config_json: '', status: 1 });
            const jsonLineNumbers = ref('1');
            let refreshTimer = null;

            const toolColumns = [
                { colKey: 'name', title: '工具名称', width: 320, ellipsis: true },
                { colKey: 'description', title: '描述', minWidth: 480, ellipsis: true },
            ];
            const serverColumns = [
                { colKey: 'name', title: '名称', ellipsis: true },
                { colKey: 'url', title: '服务地址', ellipsis: true },
                { colKey: 'status', title: '状态' },
                { colKey: 'tools', title: '工具数量' },
                { colKey: 'test', title: '最近测试' },
                { colKey: 'operation', title: '操作' },
            ];
            const responseData = (response) => response?.data?.data || response?.data || {};
            const errorText = (error, fallback) => error.response?.data?.detail || error.response?.data?.msg || fallback;
            const labels = { connected: '已连接', connecting: '连接中', reconnecting: '重连中', stopped: '已停止', disabled: '未启用', config_missing: '配置缺失' };
            const statusLabel = computed(() => labels[status.status] || status.status || '未知');
            const statusTheme = computed(() => status.status === 'connected' ? 'success' : (status.status === 'reconnecting' ? 'warning' : 'default'));
            const pendingServerCount = computed(() => externalServers.value.filter((item) => item.status === 1 && !(item.tools_cache || []).length).length);

            const validWss = (value) => {
                if (!value && (form.enabled !== 1 || config.endpoint_configured)) return true;
                try { return new URL(value).protocol === 'wss:'; } catch (error) { return false; }
            };
            const configRules = {
                endpoint_url: [{ validator: () => ({ result: validWss(form.endpoint_url.trim()), message: '接入点必须是有效的 wss:// 地址' }), type: 'error' }],
            };
            const parseSingleMcp = (raw) => {
                let parsed;
                try { parsed = JSON.parse(raw); } catch (error) { throw new Error('JSON 格式无效'); }
                if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('JSON 必须是对象');
                let item = parsed;
                if (parsed.mcpServers) {
                    const names = Object.keys(parsed.mcpServers);
                    if (names.length !== 1) throw new Error('每条配置只能包含一个 MCP 服务');
                    item = parsed.mcpServers[names[0]];
                }
                if (!item || typeof item !== 'object' || Array.isArray(item) || !item.url) throw new Error('配置必须包含服务地址');
                try {
                    const url = new URL(item.url);
                    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) throw new Error();
                } catch (error) { throw new Error('MCP 服务地址仅支持 HTTP/HTTPS'); }
                return parsed;
            };
            const serverRules = {
                name: [{ required: true, message: '请输入 MCP 名称', type: 'error' }],
                config_json: [{ validator: () => {
                    try { parseSingleMcp(serverForm.config_json.trim()); return { result: true }; }
                    catch (error) { return { result: false, message: error.message }; }
                }, type: 'error' }],
                status: [{ validator: () => ({ result: serverForm.status !== 1 || Boolean(serverForm.config_json.trim()), message: '启用前必须完成完整配置' }), type: 'error' }],
            };

            const setTabUrl = (tab) => {
                const url = new URL(window.location.href);
                url.searchParams.set('tab', tab);
                window.history.replaceState({}, '', url);
            };
            const onTabChange = (value) => { activeTab.value = value; setTabUrl(value); };
            const loadConfig = async () => {
                configLoading.value = true;
                try {
                    const res = await request.get('/xiaozhi-mcp/config');
                    Object.assign(config, responseData(res));
                    form.enabled = config.enabled ? 1 : 0;
                } finally { configLoading.value = false; }
            };
            const loadStatus = async () => {
                statusLoading.value = true;
                try {
                    const res = await request.get('/xiaozhi-mcp/status', { skipAutoError: true });
                    Object.assign(status, responseData(res));
                } catch (error) { /* 状态轮询失败由页面状态保留上次结果 */ }
                finally { statusLoading.value = false; }
            };
            const loadTools = async () => {
                toolsLoading.value = true;
                toolsError.value = '';
                try {
                    const res = await request.get('/xiaozhi-mcp/tools', { skipAutoError: true });
                    Object.assign(tools, responseData(res));
                } catch (error) {
                    tools.list = [];
                    toolsError.value = errorText(error, '工具发现失败');
                } finally { toolsLoading.value = false; }
            };
            const loadExternalServers = async () => {
                serversLoading.value = true;
                try {
                    const res = await request.get('/xiaozhi-mcp/custom-servers', { skipAutoError: true });
                    externalServers.value = responseData(res).list || [];
                } catch (error) { externalServers.value = []; }
                finally { serversLoading.value = false; }
            };
            const saveConfig = async (params) => {
                if (params?.e) params.e.preventDefault();
                if (params?.validateResult !== true) return;
                saving.value = true;
                try {
                    await request.put('/xiaozhi-mcp/config', { enabled: form.enabled === 1, endpoint_url: form.endpoint_url });
                    form.endpoint_url = '';
                    MessagePlugin.success('配置已保存');
                    await Promise.all([loadConfig(), loadStatus(), loadTools()]);
                } catch (error) { MessagePlugin.error(errorText(error, '保存失败')); }
                finally { saving.value = false; }
            };
            const clearEndpoint = () => {
                const dialog = DialogPlugin.confirm({
                    header: '清除接入点',
                    body: '清除后常驻连接将停止。',
                    onConfirm: async () => {
                        try {
                            await request.put('/xiaozhi-mcp/config', { enabled: false, clear_endpoint_url: true });
                            MessagePlugin.success('配置已清除');
                            await Promise.all([loadConfig(), loadStatus(), loadTools()]);
                        } catch (error) { MessagePlugin.error(errorText(error, '清除失败')); }
                        finally { dialog.destroy(); }
                    },
                });
            };
            const runAction = async (path, success) => {
                operating.value = true;
                try { await request.post(path); MessagePlugin.success(success); await loadStatus(); }
                catch (error) { MessagePlugin.error(errorText(error, '操作失败')); }
                finally { operating.value = false; }
            };
            const testConnection = async () => {
                testing.value = true;
                try { await request.post('/xiaozhi-mcp/test'); MessagePlugin.success('连接测试通过'); await Promise.all([loadStatus(), loadTools()]); }
                catch (error) { MessagePlugin.error(errorText(error, '连接测试失败')); }
                finally { testing.value = false; }
            };
            const updateJsonLines = () => {
                const count = (serverForm.config_json || '').split('\n').length;
                jsonLineNumbers.value = Array.from({ length: count }, (_, index) => index + 1).join('\n');
            };
            const openServerCreate = () => {
                Object.assign(serverForm, { id: 0, name: '', config_json: '', status: 1 });
                updateJsonLines();
                serverVisible.value = true;
            };
            const openServerEdit = (row) => {
                Object.assign(serverForm, { id: row.id, name: row.name, config_json: row.config_json || '', status: row.status });
                updateJsonLines();
                serverVisible.value = true;
            };
            const saveServer = async (params) => {
                if (params?.e) params.e.preventDefault();
                if (params?.validateResult !== true) return;
                serverSaving.value = true;
                try {
                    const payload = { name: serverForm.name.trim(), config_json: serverForm.config_json, status: serverForm.status };
                    if (serverForm.id) await request.put(`/xiaozhi-mcp/custom-servers/${serverForm.id}`, payload);
                    else await request.post('/xiaozhi-mcp/custom-servers', payload);
                    MessagePlugin.success('MCP 配置已保存');
                    serverVisible.value = false;
                    await Promise.all([loadExternalServers(), loadTools()]);
                } catch (error) { MessagePlugin.error(errorText(error, '保存 MCP 失败')); }
                finally { serverSaving.value = false; }
            };
            const toggleServer = async (row) => {
                try {
                    await request.put(`/xiaozhi-mcp/custom-servers/${row.id}`, { name: row.name, config_json: row.config_json, status: row.status === 1 ? 2 : 1 });
                    MessagePlugin.success(row.status === 1 ? 'MCP 已停用' : 'MCP 已启用');
                    await Promise.all([loadExternalServers(), loadTools()]);
                } catch (error) { MessagePlugin.error(errorText(error, '状态更新失败')); }
            };
            const deleteServer = (row) => {
                const dialog = DialogPlugin.confirm({
                    header: '删除 MCP', body: `确定删除「${row.name}」吗？`,
                    onConfirm: async () => {
                        try { await request.delete(`/xiaozhi-mcp/custom-servers/${row.id}`); MessagePlugin.success('MCP 已删除'); await Promise.all([loadExternalServers(), loadTools()]); }
                        catch (error) { MessagePlugin.error(errorText(error, '删除失败')); }
                        finally { dialog.destroy(); }
                    },
                });
            };
            const testServer = async (row) => {
                row.testing = true;
                try {
                    const res = await request.post(`/xiaozhi-mcp/custom-servers/${row.id}/test`);
                    const data = responseData(res);
                    if (data.success) MessagePlugin.success(data.message || '连接成功');
                    else MessagePlugin.warning(data.message || '连接失败');
                    await Promise.all([loadExternalServers(), loadTools()]);
                } catch (error) { MessagePlugin.error(errorText(error, '测试失败')); }
                finally { row.testing = false; }
            };
            const refreshServerTools = async (row) => {
                row.refreshing = true;
                try {
                    const res = await request.get(`/xiaozhi-mcp/custom-servers/${row.id}/tools`);
                    const data = responseData(res);
                    row.tools_cache = data.list || [];
                    if (data.message) MessagePlugin.warning(data.message);
                    else MessagePlugin.success('工具列表已刷新');
                    await Promise.all([loadExternalServers(), loadTools()]);
                } catch (error) { MessagePlugin.error(errorText(error, '工具刷新失败')); }
                finally { row.refreshing = false; }
            };
            onMounted(async () => {
                if (query.get('tab') === 'runtime') setTabUrl('config');
                await Promise.all([loadConfig(), loadStatus(), loadTools(), loadExternalServers()]);
                refreshTimer = window.setInterval(loadStatus, 5000);
            });
            onBeforeUnmount(() => { if (refreshTimer) window.clearInterval(refreshTimer); });
            return {
                activeTab, config, form, status, tools, externalServers,
                configLoading, statusLoading, toolsLoading, toolsError, serversLoading, saving, testing, operating,
                serverVisible, serverSaving, serverFormRef, serverForm, jsonLineNumbers,
                toolColumns, serverColumns, statusLabel, statusTheme, pendingServerCount,
                configRules, serverRules, onTabChange, saveConfig, clearEndpoint,
                testConnection, reconnect: () => runAction('/xiaozhi-mcp/reconnect', '已提交重连'),
                disconnect: () => runAction('/xiaozhi-mcp/disconnect', '连接已断开'),
                openServerCreate, openServerEdit, saveServer, deleteServer, toggleServer,
                testServer, refreshServerTools, updateJsonLines,
            };
        },
    });
    request.get('/permission/my-auth', { skipAutoError: true }).then((res) => {
        const data = res?.data?.data || res?.data || {};
        localStorage.setItem('admin_auth', JSON.stringify(data.auth || []));
        localStorage.setItem('admin_pages', JSON.stringify(data.pages || {}));
    }).catch(() => { /* 登录失效由公共请求层统一处理 */ }).finally(mountPage);
})();
