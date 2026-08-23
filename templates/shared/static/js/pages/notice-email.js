/**
 * 邮件通知页 — 两Tab结构（接口列表 + 模板管理）
 *
 * 对标 ZJMF notice_email：
 * - Tab1: 插件接口列表（安装/卸载/配置/启停）
 * - Tab2: 邮件模板 CRUD（跳转独立编辑页 TinyMCE）+ 测试发送
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
                const res = await request.get('/notice/interfaces/list', { params: { module: 'mail' } });
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

        // 配置弹窗
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
        // Tab2: 模板管理（跳转独立编辑页）
        // ============================================================
        const templateList = ref([]);
        const templateLoading = ref(false);
        const tplSearchKeyword = ref('');
        const templatePagination = reactive({ current: 1, pageSize: 20, total: 0 });
        const templateColumns = [
            { colKey: 'id', title: 'ID', width: 60 },
            { colKey: 'name', title: '模板名称', minWidth: 140 },
            { colKey: 'subject', title: '邮件主题', minWidth: 160, ellipsis: true },
            { colKey: 'operation', title: '操作', width: 200, fixed: 'right' },
        ];

        async function fetchTemplates() {
            templateLoading.value = true;
            try {
                var params = {
                    page: templatePagination.current,
                    limit: templatePagination.pageSize,
                };
                if (tplSearchKeyword.value) params.keywords = tplSearchKeyword.value;
                const res = await request.get('/notice/email-templates/list', { params: params });
                var payload = res.data.data || res.data;
                templateList.value = payload.list || [];
                templatePagination.total = payload.total || 0;
            } finally {
                templateLoading.value = false;
            }
        }

        function onTplPageChange(e) {
            templatePagination.current = e.current;
            templatePagination.pageSize = e.pageSize;
            fetchTemplates();
        }

        function goCreate() {
            HuiYan.loadPage('/admin/notice-email-template-add');
        }

        function goEdit(row) {
            HuiYan.loadPage('/admin/notice-email-template-add?id=' + row.id);
        }

        async function deleteTemplate(row) {
            var ok = await confirmAsync({ header: '确认删除', body: '确定删除模板「' + row.name + '」吗？' });
            if (ok) {
                await request.delete('/notice/email-templates/' + row.id);
                MessagePlugin.success('已删除');
                fetchTemplates();
            }
        }

        // 测试发送
        const testDialogVisible = ref(false);
        const testTemplate = ref(null);
        const testVariables = ref([]);
        const testForm = reactive({ email: '', vars: {} });
        const testSending = ref(false);

        function testSend(row) {
            testTemplate.value = row;
            testForm.email = '';
            testForm.vars = {};
            var textContent = (row.content || '').replace(/<[^>]+>/g, '');
            var matches = textContent.match(/\{(\w+)\}/g) || [];
            var seen = {};
            testVariables.value = matches.map(function (m) {
                return m.replace(/[{}]/g, '');
            }).filter(function (v) {
                if (seen[v]) return false;
                seen[v] = true;
                return true;
            });
            testDialogVisible.value = true;
        }

        function doTestSend(params) {
            if (params && params.e) params.e.preventDefault();
            doSendTest();
        }

        async function doSendTest() {
            testSending.value = true;
            try {
                var res = await request.post('/notice/email-templates/test-send', {
                    template_id: testTemplate.value.id,
                    recipient: testForm.email,
                    variables: testForm.vars,
                });
                if (res.data && res.data.status === 200) {
                    MessagePlugin.success(res.data.msg || '发送成功');
                    testDialogVisible.value = false;
                }
            } catch (e) {
                // 全局拦截器已处理错误提示
            } finally {
                testSending.value = false;
            }
        }

        // ============================================================
        // 初始化
        // ============================================================
        onMounted(function () {
            fetchPlugins();
            fetchTemplates();
        });

        return {
            activeTab,
            // Tab1
            pluginList, pluginLoading, pluginColumns,
            installPlugin, uninstallPlugin, togglePlugin, testPlugin,
            configVisible, configPluginTitle, configSchema, configForm, configRules, configSaving,
            openConfig, saveConfig,
            // Tab2
            templateList, templateLoading, tplSearchKeyword,
            templatePagination, templateColumns, fetchTemplates, onTplPageChange,
            goCreate, goEdit, deleteTemplate,
            testDialogVisible, testTemplate, testVariables, testForm, testSending, testSend, doTestSend,
        };
    },
});
})();
