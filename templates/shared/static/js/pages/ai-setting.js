/**
 * AI 设置页 — 三 Tab 结构（对标通知模块设置页）
 *
 * Tab1 接口列表: llm 驱动插件 安装/卸载/配置/测试/启停/激活
 * Tab2 对话参数: 全局 ai.* 参数表单
 * Tab3 MCP 能力: 管理员 + 农户端外部 MCP 服务器 CRUD/连通测试
 */
(function () {
HuiYan.createPage({
    setup() {
        const { ref, reactive, onMounted, watch, nextTick } = Vue;
        const { MessagePlugin, DialogPlugin } = TDesign;

        // 封装 DialogPlugin.confirm 为 Promise（TDesign 原生是回调式）
        function confirmAsync(options) {
            return new Promise(function (resolve) {
                var instance = DialogPlugin.confirm({
                    header: options.header,
                    body: options.body,
                    onConfirm: function () { instance.destroy(); resolve(true); },
                    onClose: function () { instance.destroy(); resolve(false); },
                    onCancel: function () { instance.destroy(); resolve(false); }
                });
            });
        }

        // MCP 子 Tab 状态：管理员 / 农户端竖向切换
        const mcpActiveTab = ref('admin');

        // 主 Tab 状态：URL ?tab= 双向同步（刷新不丢 Tab）
        const activeTab = ref(HuiYan.getUrlTab('interfaces', ['interfaces', 'params', 'mcp']));
        watch(activeTab, function (val) { HuiYan.syncUrlTab(val); });

        /* ============================================================
         * Tab1: 接口列表（llm 驱动插件）
         * ============================================================ */
        const pluginList = ref([]);
        const pluginLoading = ref(false);
        const pluginColumns = [
            { colKey: 'title', title: '插件名称', minWidth: 160 },
            { colKey: 'description', title: '描述', ellipsis: true },
            { colKey: 'version', title: '版本', width: 90 },
            { colKey: 'status', title: '状态', width: 110 },
            { colKey: 'active', title: '激活', width: 110 },
            { colKey: 'operation', title: '操作', width: 360, fixed: 'right' },
        ];

        async function fetchPlugins() {
            pluginLoading.value = true;
            try {
                const res = await request.get('/ai/setting/interfaces');
                pluginList.value = (res.data.data || {}).list || [];
            } finally {
                pluginLoading.value = false;
            }
        }

        async function installPlugin(row) {
            try {
                await request.post('/plugin/install/' + row.name);
                MessagePlugin.success('插件安装成功');
                fetchPlugins();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '安装失败');
            }
        }

        async function uninstallPlugin(row) {
            var yes = await confirmAsync({ header: '确认卸载', body: '确定卸载插件「' + row.title + '」吗？配置数据不会丢失。' });
            if (!yes) return;
            try {
                await request.post('/plugin/uninstall/' + row.name);
                MessagePlugin.success('已卸载');
                fetchPlugins();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '卸载失败');
            }
        }

        async function upgradePlugin(row) {
            var yes = await confirmAsync({
                header: '确认升级',
                body: '将插件「' + row.title + '」从 ' + row.installed_version
                    + ' 升级到 ' + row.version + '，是否继续？'
            });
            if (!yes) return;
            try {
                await request.post('/plugin/upgrade/' + row.name);
                MessagePlugin.success('插件升级成功');
                fetchPlugins();
                fetchModels();
                fetchVisionModels();
            } catch (e) {
                var detail = e.response?.data?.detail;
                MessagePlugin.error(detail?.message || detail || '升级失败');
            }
        }

        async function togglePlugin(row, status) {
            try {
                await request.put('/ai/setting/interfaces/' + row.name + '/status', { status: status });
                MessagePlugin.success(status === 1 ? '已启用' : '已禁用');
                fetchPlugins();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '操作失败');
            }
        }

        async function testPlugin(row) {
            MessagePlugin.info('正在测试连接...');
            try {
                const res = await request.post('/ai/setting/interfaces/' + row.name + '/test');
                var td = res.data.data || {};
                if (td.success) {
                    MessagePlugin.success(td.message || '连接成功');
                } else {
                    MessagePlugin.warning('连接失败: ' + (td.message || '未知错误'));
                }
            } catch (e) {
                MessagePlugin.error('测试失败');
            }
        }

        // 激活 = 写全局参数 ai.active_interface（对话默认走该驱动）
        async function activatePlugin(row) {
            try {
                await request.put('/ai/setting/params', {
                    active_interface: row.name
                });
                MessagePlugin.success('已激活接口「' + row.title + '」');
                fetchPlugins();
                fetchModels();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '激活失败');
            }
        }

        /* ===== 配置弹窗（schema 驱动渲染） ===== */
        const configVisible = ref(false);
        const configSaving = ref(false);
        const configPluginTitle = ref('');
        const configPluginName = ref('');
        const configSchema = ref([]);
        const configForm = reactive({});

        function openConfig(row) {
            configPluginTitle.value = row.title;
            configPluginName.value = row.name;
            configSchema.value = row.config_schema || [];
            Object.keys(configForm).forEach(function (k) { delete configForm[k]; });
            configSchema.value.forEach(function (f) {
                configForm[f.key] = (row.config || {})[f.key] ?? f.default ?? '';
            });
            configVisible.value = true;
        }

        async function saveConfig() {
            configSaving.value = true;
            try {
                await request.put('/ai/setting/interfaces/' + configPluginName.value + '/config',
                    { config: Object.assign({}, configForm) });
                MessagePlugin.success('配置已保存');
                configVisible.value = false;
                fetchPlugins();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '保存失败');
            } finally {
                configSaving.value = false;
            }
        }

        /* ============================================================
         * Tab2: 对话参数
         * ============================================================ */
        const paramsForm = reactive({
            default_model: '', vision_model: 'glm-4.6v-flash',
            max_tool_rounds: 5, context_limit: 20, farmer_enabled: false,
            system_tools_enabled: false,
            reasoning_effort: 'high',
            system_prompt: '', temperature: 0.7, max_tokens: 0,
        });
        const paramsSaving = ref(false);
        const models = ref([]);
        const modelsLoading = ref(false);
        const visionModels = ref([]);
        const visionModelsLoading = ref(false);

        async function fetchParams() {
            try {
                const res = await request.get('/ai/setting/params');
                const d = res.data.data || {};
                paramsForm.default_model = d.default_model || '';
                paramsForm.vision_model = d.vision_model || 'glm-4.6v-flash';
                paramsForm.max_tool_rounds = d.max_tool_rounds || 5;
                paramsForm.context_limit = d.context_limit || 20;
                paramsForm.farmer_enabled = !!d.farmer_enabled;
                paramsForm.system_tools_enabled = !!d.system_tools_enabled;
                paramsForm.reasoning_effort = d.reasoning_effort || 'high';
                paramsForm.system_prompt = d.system_prompt || '';
                paramsForm.temperature = d.temperature !== undefined ? d.temperature : 0.7;
                paramsForm.max_tokens = d.max_tokens !== undefined ? d.max_tokens : 0;
            } catch (e) { /* 静默 */ }
        }

        async function fetchModels() {
            modelsLoading.value = true;
            try {
                const res = await request.get('/ai/setting/models');
                models.value = (res.data.data || {}).list || [];
                if (!models.value.some(function (item) {
                    return item.name === paramsForm.default_model;
                })) {
                    paramsForm.default_model = '';
                }
            } catch (e) {
                models.value = [];
                paramsForm.default_model = '';
            } finally {
                modelsLoading.value = false;
            }
        }

        async function fetchVisionModels() {
            visionModelsLoading.value = true;
            try {
                const res = await request.get('/ai/setting/models', {
                    params: { interface: 'vision_glm' }
                });
                const list = (res.data.data || {}).list || [];
                visionModels.value = list.filter(function (item) {
                    return item.support_vision === true;
                });
                if (!visionModels.value.some(function (item) {
                    return item.name === paramsForm.vision_model;
                })) {
                    paramsForm.vision_model = '';
                }
            } catch (e) {
                visionModels.value = [];
                paramsForm.vision_model = '';
            } finally {
                visionModelsLoading.value = false;
            }
        }

        async function saveParams() {
            paramsSaving.value = true;
            try {
                await request.put('/ai/setting/params', {
                    default_model: paramsForm.default_model,
                    vision_model: paramsForm.vision_model,
                    max_tool_rounds: paramsForm.max_tool_rounds,
                    context_limit: paramsForm.context_limit,
                    farmer_enabled: paramsForm.farmer_enabled,
                    system_tools_enabled: paramsForm.system_tools_enabled,
                    reasoning_effort: paramsForm.reasoning_effort,
                    system_prompt: paramsForm.system_prompt,
                    temperature: paramsForm.temperature,
                    max_tokens: paramsForm.max_tokens,
                });
                MessagePlugin.success('参数已保存');
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '保存失败');
            } finally {
                paramsSaving.value = false;
            }
        }

        /* ============================================================
         * Tab3: MCP 能力（外部 MCP 服务器 CRUD）
         * 管理员与农户端共用工厂函数，仅 URL 前缀与提示文案不同
         * ============================================================ */
        const serverColumns = [
            { colKey: 'name', title: '名称', minWidth: 140 },
            { colKey: 'url', title: '服务地址', ellipsis: true },
            { colKey: 'status', title: '状态', width: 90 },
            { colKey: 'create_time', title: '添加时间', width: 180 },
            { colKey: 'operation', title: '操作', width: 180, fixed: 'right' },
        ];
        const serverToolColumns = [
            { colKey: 'name', title: '工具名称', minWidth: 200 },
            { colKey: 'description', title: '描述', ellipsis: true },
        ];

        /* 服务器 CRUD 工厂：baseUrl 为接口路径前缀，label 为提示文案前缀 */
        function createServerCrud(baseUrl, label) {
            const list = ref([]);
            const loading = ref(false);
            const expandedIds = ref([]);
            const toolsLoading = reactive({});
            const visible = ref(false);
            const saving = ref(false);
            const form = reactive({ id: 0, name: '', config_json: '', status: 1 });
            const jsonLines = ref('1');

            function updateLines() {
                var text = form.config_json || '';
                var n = text.split('\n').length;
                jsonLines.value = Array.from({length: n}, function(_, i) { return i + 1; }).join('\n');
            }
            async function fetchList() {
                loading.value = true;
                try {
                    const res = await request.get(baseUrl);
                    list.value = (res.data.data || {}).list || [];
                } finally { loading.value = false; }
            }
            function openCreate() {
                visible.value = true;
                nextTick(function () {
                    Object.assign(form, { id: 0, name: '', config_json: '', status: 1 });
                    updateLines();
                });
            }
            function openEdit(row) {
                visible.value = true;
                nextTick(function () {
                    Object.assign(form, { id: row.id, name: row.name,
                        config_json: row.config_json || '', status: row.status });
                    updateLines();
                });
            }
            async function save() {
                if (!form.name.trim()) { MessagePlugin.warning('请填写服务器名称'); return; }
                saving.value = true;
                const payload = { name: form.name, config_json: form.config_json, status: form.status };
                try {
                    if (form.id) { await request.put(baseUrl + '/' + form.id, payload); }
                    else { await request.post(baseUrl, payload); }
                    MessagePlugin.success(label + '服务器已保存');
                    visible.value = false;
                    fetchList();
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '保存失败');
                } finally { saving.value = false; }
            }
            async function del(row) {
                var yes = await confirmAsync({ header: '删除服务器',
                    body: '确定删除' + label + ' MCP 服务器「' + row.name + '」吗？' });
                if (!yes) return;
                try {
                    await request.delete(baseUrl + '/' + row.id);
                    MessagePlugin.success(label + '服务器已删除');
                    fetchList();
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '删除失败');
                }
            }
            async function test(row) {
                MessagePlugin.info('正在测试连通性...');
                try {
                    const res = await request.post(baseUrl + '/' + row.id + '/test');
                    var td = res.data.data || {};
                    if (td.success) { MessagePlugin.success(td.message || '连接成功'); fetchList(); }
                    else { MessagePlugin.warning(td.message || '连接失败'); }
                } catch (e) { MessagePlugin.error('测试请求失败'); }
            }
            async function onExpand(expandedKeys, context) {
                expandedIds.value = expandedKeys;
                var row = context.currentRowData;
                if (context.expanded && row && !(row.tools_cache && row.tools_cache.length)) {
                    toolsLoading[row.id] = true;
                    try {
                        const res = await request.get(baseUrl + '/' + row.id + '/tools');
                        var data = res.data.data || {};
                        if (data.list && data.list.length) { row.tools_cache = data.list; }
                    } catch (e) { /* 静默 */ } finally { toolsLoading[row.id] = false; }
                }
            }
            return { list, loading, expandedIds, toolsLoading, visible, saving, form,
                     jsonLines, updateLines, fetchList, openCreate, openEdit, save, del, test, onExpand };
        }

        const adminCrud = createServerCrud('/ai/setting/mcp-servers', '');
        const farmerCrud = createServerCrud('/ai/setting/mcp-servers-farmer', '农户端');

        async function loadParamsAndModels() {
            // 先读取参数，再用实际可用模型校正旧的默认值，避免并发响应覆盖清空结果。
            await fetchParams();
            await fetchModels();
            await fetchVisionModels();
        }

        onMounted(function () {
            fetchPlugins();
            loadParamsAndModels();
            adminCrud.fetchList();
            farmerCrud.fetchList();
        });

        return {
            activeTab,
            // Tab1
            pluginList, pluginLoading, pluginColumns,
            installPlugin, uninstallPlugin, upgradePlugin,
            togglePlugin, testPlugin, activatePlugin,
            configVisible, configSaving, configPluginTitle, configSchema, configForm,
            openConfig, saveConfig,
            // Tab2
            paramsForm, paramsSaving, models, modelsLoading,
            visionModels, visionModelsLoading, saveParams,
            // Tab3 - MCP 子 Tab
            mcpActiveTab,
            // Tab3 - 管理员
            jsonLineNumbers: adminCrud.jsonLines, updateJsonLines: adminCrud.updateLines,
            serverList: adminCrud.list, serverLoading: adminCrud.loading,
            expandedServerIds: adminCrud.expandedIds, serverToolsLoading: adminCrud.toolsLoading,
            onServerExpand: adminCrud.onExpand,
            serverVisible: adminCrud.visible, serverSaving: adminCrud.saving, serverForm: adminCrud.form,
            openServerCreate: adminCrud.openCreate, openServerEdit: adminCrud.openEdit,
            saveServer: adminCrud.save, deleteServer: adminCrud.del, testServer: adminCrud.test,
            // Tab3 - 农户端
            farmerJsonLineNumbers: farmerCrud.jsonLines, updateFarmerJsonLines: farmerCrud.updateLines,
            farmerServerList: farmerCrud.list, farmerServerLoading: farmerCrud.loading,
            expandedFarmerServerIds: farmerCrud.expandedIds, farmerServerToolsLoading: farmerCrud.toolsLoading,
            onFarmerServerExpand: farmerCrud.onExpand,
            farmerServerVisible: farmerCrud.visible, farmerServerSaving: farmerCrud.saving,
            farmerServerForm: farmerCrud.form,
            openFarmerServerCreate: farmerCrud.openCreate, openFarmerServerEdit: farmerCrud.openEdit,
            saveFarmerServer: farmerCrud.save, deleteFarmerServer: farmerCrud.del,
            testFarmerServer: farmerCrud.test,
            // 共享列定义
            serverColumns, serverToolColumns,
        };
    }
});
})();
