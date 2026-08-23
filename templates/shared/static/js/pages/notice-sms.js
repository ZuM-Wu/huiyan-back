/**
 * 短信通知页 — 两Tab结构（接口列表 + 模板管理）
 *
 * 对标 ZJMF notice_sms：
 * - Tab1: 插件接口列表（安装/卸载/配置/启停）
 * - Tab2: 短信模板 CRUD + 测试发送
 * 发送设置已抽离到独立页面 /admin/notice-send
 */
(function () {
HuiYan.createPage({
    setup() {
        const { ref, reactive, onMounted, computed, watch } = Vue;
        const { MessagePlugin, DialogPlugin } = TDesign;

        // 封装 DialogPlugin.confirm 为 Promise（TDesign 原生是回调式）
        function confirmAsync(options) {
            return new Promise(function(resolve) {
                var instance = DialogPlugin.confirm({
                    header: options.header,
                    body: options.body,
                    onConfirm: function() { instance.destroy(); resolve(true); },
                    onClose: function() { instance.destroy(); resolve(false); },
                    onCancel: function() { instance.destroy(); resolve(false); }
                });
            });
        }

        // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
        const activeTab = ref(HuiYan.getUrlTab('interfaces', ['interfaces', 'templates']));
        watch(activeTab, function (val) { HuiYan.syncUrlTab(val); });

        // ============================================================
        // Tab1: 接口列表
        // ============================================================
        const pluginList = ref([]);
        const pluginLoading = ref(false);

        async function fetchPlugins() {
            pluginLoading.value = true;
            try {
                const res = await request.get('/notice/interfaces/list', { params: { module: 'sms' } });
                // 接口列表端点返回统一响应信封，插件列表位于 data 层
                pluginList.value = (res.data.data || {}).list || [];
            } finally {
                pluginLoading.value = false;
            }
        }

        const pluginColumns = [
            { colKey: 'title', title: '插件名称', minWidth: 160 },
            { colKey: 'version', title: '版本', width: 100 },
            { colKey: 'status', title: '状态', width: 120 },
            { colKey: 'operation', title: '操作', width: 280, fixed: 'right' },
        ];

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
            var ok = await confirmAsync({ header: '确认卸载', body: '确定卸载插件「' + row.title + '」吗？卸载后模板数据不会丢失。' });
            if (ok) {
                try {
                    await request.post('/plugin/uninstall/' + row.name);
                    MessagePlugin.success('已卸载');
                    fetchPlugins();
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '卸载失败');
                }
            }
        }

        async function togglePlugin(row, status) {
            try {
                await request.put('/notice/interfaces/' + row.name + '/status', { status: status });
                MessagePlugin.success(status === 1 ? '已启用' : '已禁用');
                fetchPlugins();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '操作失败');
            }
        }

        async function testPlugin(row) {
            MessagePlugin.info('正在测试连接...');
            try {
                const res = await request.post('/notice/interfaces/' + row.name + '/test');
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

        async function syncTemplates(row) {
            MessagePlugin.info('正在同步远程模板...');
            try {
                const res = await request.post('/notice/interfaces/' + row.name + '/sync-templates');
                var td = res.data.data || {};
                if (td.success) {
                    MessagePlugin.success(td.message + (td.synced > 0 ? '，新增 ' + td.synced + ' 条' : ''));
                    // 切换到模板管理 Tab 刷新列表
                    if (td.synced > 0) {
                        activeTab.value = 'templates';
                        fetchTemplates();
                    }
                } else {
                    MessagePlugin.warning('同步失败: ' + (td.message || '未知错误'));
                }
            } catch (e) {
                MessagePlugin.error('同步请求失败');
            }
        }

        // 配置弹窗（所有插件共用）
        const configVisible = ref(false);
        const configPluginName = ref('');
        const configPluginTitle = ref('');
        const configSchema = ref([]);
        const configForm = reactive({});
        const configSaving = ref(false);

        const configRules = computed(function () {
            var rules = {};
            configSchema.value.forEach(function (f) {
                if (f.required) {
                    rules[f.key] = [{ required: true, message: '请输入' + f.label, type: 'error' }];
                }
            });
            return rules;
        });

        function openConfig(row) {
            configPluginName.value = row.name;
            configPluginTitle.value = row.title;
            configSchema.value = row.config_schema || [];
            Object.keys(configForm).forEach(function (k) { delete configForm[k]; });
            (row.config_schema || []).forEach(function (f) {
                configForm[f.key] = (row.config || {})[f.key] !== undefined ? (row.config || {})[f.key] : (f.default || '');
            });
            configVisible.value = true;
        }

        function saveConfig(params) {
            if (params && params.e) params.e.preventDefault();
            if (params && params.validateResult !== true) return;
            doSaveConfig();
        }

        async function doSaveConfig() {
            configSaving.value = true;
            try {
                await request.put('/notice/interfaces/' + configPluginName.value + '/config', { config: { ...configForm } });
                MessagePlugin.success('配置已保存');
                configVisible.value = false;
                fetchPlugins();
            } finally {
                configSaving.value = false;
            }
        }

        // ============================================================
        // Tab2: 模板管理
        // ============================================================
        const templateList = ref([]);
        const templateLoading = ref(false);
        const tplSearchKeyword = ref('');
        const tplFilterType = ref(null);
        const tplFilterInterface = ref(null);
        const templatePagination = reactive({ current: 1, pageSize: 20, total: 0 });
        const templateColumns = [
            { colKey: 'id', title: 'ID', width: 60 },
            { colKey: 'template_id', title: '模板ID', width: 120 },
            { colKey: 'interface', title: '来源', width: 120 },
            { colKey: 'type', title: '类型', width: 80 },
            { colKey: 'title', title: '标题', minWidth: 140 },
            { colKey: 'content', title: '内容', minWidth: 200, ellipsis: true },
            { colKey: 'status', title: '状态', width: 100 },
            { colKey: 'operation', title: '操作', width: 260, fixed: 'right' },
        ];

        async function fetchTemplates() {
            templateLoading.value = true;
            try {
                var params = {
                    page: templatePagination.current,
                    limit: templatePagination.pageSize,
                };
                if (tplFilterInterface.value) params.interface = tplFilterInterface.value;
                if (tplFilterType.value !== null) params.type = tplFilterType.value;
                if (tplSearchKeyword.value) params.keywords = tplSearchKeyword.value;
                const res = await request.get('/notice/sms-templates/list', { params: params });
                templateList.value = (res.data.data || res.data).list || [];
                templatePagination.total = (res.data.data || res.data).total || 0;
            } finally {
                templateLoading.value = false;
            }
        }

        function onTplPageChange(e) {
            templatePagination.current = e.current;
            templatePagination.pageSize = e.pageSize;
            fetchTemplates();
        }

        // 模板编辑弹窗
        const dialogVisible = ref(false);
        const dialogTitle = ref('');
        const tplFormRef = ref(null);
        const saving = ref(false);
        const templateForm = reactive({
            id: 0, template_id: '', type: 0, title: '', content: '',
            status: 0, interface: '', remark: '', action_key: '', is_local: false,
        });
        var isEdit = false;

        const tplRules = {
            title: [{ required: true, message: '请输入模板标题', type: 'error' }],
            content: [
                { required: true, message: '请输入模板内容', type: 'error' },
                { max: 500, message: '内容不能超过500字', type: 'warning' },
            ],
        };

        // 通知动作列表（下拉选择）
        const actionList = ref([]);

        async function fetchActions() {
            try {
                const res = await request.get('/notice/actions/list', { params: { limit: 100 } });
                actionList.value = (res.data.data || res.data).list || [];
            } catch (e) { /* 静默 */ }
        }

        // 可用接口选项（用于模板 interface 字段下拉）
        const installedPlugins = computed(function () {
            return pluginList.value.filter(function (p) { return p.installed; });
        });

        function openCreate() {
            isEdit = false;
            dialogTitle.value = '新建短信模板';
            var defaultIface = installedPlugins.value.length > 0 ? installedPlugins.value[0].name : '';
            Object.assign(templateForm, {
                id: 0, template_id: '', type: 0, title: '', content: '',
                status: 0, interface: defaultIface, remark: '', action_key: '', is_local: false,
            });
            dialogVisible.value = true;
        }

        function openEdit(row) {
            isEdit = true;
            dialogTitle.value = '编辑短信模板';
            Object.assign(templateForm, {
                id: row.id, template_id: row.template_id || '', type: row.type || 0,
                title: row.title || '', content: row.content || '',
                status: row.status || 0, interface: row.interface || '',
                remark: row.remark || '', action_key: row.action_key || '',
                is_local: Boolean(row.is_local),
            });
            dialogVisible.value = true;
        }

        function saveTemplate(params) {
            if (params && params.e) params.e.preventDefault();
            if (params && params.validateResult !== true) return;
            doSaveTemplate();
        }

        async function doSaveTemplate() {
            saving.value = true;
            try {
                if (isEdit) {
                    await request.put('/notice/sms-templates/' + templateForm.id, { ...templateForm });
                } else {
                    await request.post('/notice/sms-templates/', { ...templateForm });
                }
                MessagePlugin.success('保存成功');
                dialogVisible.value = false;
                fetchTemplates();
            } finally {
                saving.value = false;
            }
        }

        function closeDialog() { dialogVisible.value = false; }

        async function deleteTemplate(row) {
            var ok = await confirmAsync({ header: '确认删除', body: '确定删除模板「' + row.title + '」吗？' });
            if (ok) {
                await request.delete('/notice/sms-templates/' + row.id);
                MessagePlugin.success('已删除');
                fetchTemplates();
            }
        }

        async function submitReview(row) {
            try {
                var res = await request.post('/notice/sms-templates/submit', null, { params: { template_ids: row.id } });
                var data = res.data.data || res.data || {};
                if (data.errors && data.errors.length > 0) {
                    MessagePlugin.warning('提交完成，部分失败：' + data.errors[0]);
                } else {
                    MessagePlugin.success('已提交审核');
                }
                fetchTemplates();
                // 提交后自动同步平台审核状态
                if (row.interface) {
                    try {
                        await request.post('/notice/sms-templates/sync-status', null, { params: { interface: row.interface } });
                        fetchTemplates();
                    } catch (e) { /* 同步失败静默 */ }
                }
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '提交失败');
            }
        }

        // 测试发送
        const testDialogVisible = ref(false);
        const testTemplate = ref(null);
        const testVariables = ref([]);
        const testForm = reactive({ phone: '', vars: {} });
        const testSending = ref(false);

        function testSend(row) {
            testTemplate.value = row;
            testForm.phone = '';
            testForm.vars = {};
            var matches = (row.content || '').match(/@var\(([^)]+)\)/g) || [];
            testVariables.value = matches.map(function (m) { return m.replace(/@var\(|\)/g, ''); });
            testDialogVisible.value = true;
        }

        function doTestSend(params) {
            if (params && params.e) params.e.preventDefault();
            doSendTest();
        }

        async function doSendTest() {
            testSending.value = true;
            try {
                var res = await request.post('/notice/sms-templates/test-send', {
                    template_id: testTemplate.value.id,
                    recipient: testForm.phone,
                    variables: testForm.vars,
                }, { skipAutoError: true });
                var data = res.data || {};
                // 根据响应显示结果（拦截器已通过 _handled 标记跳过）
                if (data.status === 200 || data.status === 0) {
                    MessagePlugin.success(data.msg || '发送成功');
                    testDialogVisible.value = false;
                } else {
                    MessagePlugin.error(data.msg || '发送失败');
                }
            } catch (e) {
                // HTTP 4xx/5xx 由拦截器自动展示 detail
                if (!e.response) {
                    MessagePlugin.error('网络异常，请稍后重试');
                }
            } finally {
                testSending.value = false;
            }
        }

        // ============================================================
        // 初始化
        // ============================================================
        onMounted(function () {
            fetchPlugins();
            fetchActions();
            fetchTemplates();
        });

        return {
            activeTab,
            // Tab1
            pluginList, pluginLoading, pluginColumns,
            installPlugin, uninstallPlugin, togglePlugin, testPlugin, syncTemplates,
            configVisible, configPluginTitle, configSchema, configForm, configRules, configSaving,
            openConfig, saveConfig,
            // Tab2
            templateList, templateLoading, tplSearchKeyword, tplFilterType, tplFilterInterface,
            templatePagination, templateColumns, fetchTemplates, onTplPageChange,
            installedPlugins, dialogVisible, dialogTitle, tplFormRef, templateForm, tplRules,
            actionList, saving,
            openCreate, openEdit, saveTemplate, closeDialog, deleteTemplate, submitReview,
            testDialogVisible, testTemplate, testVariables, testForm, testSending, testSend, doTestSend,
        };
    },
});
})();
