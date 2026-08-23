/** 企业微信管理员通知管理页面。 */
(function () {
    'use strict';

    const { ref, reactive, computed, watch } = Vue;
    const { MessagePlugin } = TDesign;
    const TABS = ['config', 'actions', 'send', 'logs'];

    HuiYan.createPluginPage({ plugin: 'wecom_webhook', page: 'wecom_webhook',
        setup() {
            const activeTab = ref(HuiYan.getUrlTab('config', TABS));
            const loaded = reactive({ config: false, actions: false, logs: false });
            const configLoading = ref(false);
            const configSaving = ref(false);
            const configSkeleton = [[{ width: '100%' }], [{ width: '75%' }], [{ width: '100%' }]];
            const configForm = ref({ webhook_url: '', enabled: 0, timeout_seconds: 10, retry_times: 3 });

            const fetchConfig = async (force) => {
                if (loaded.config && !force) return;
                configLoading.value = true;
                try {
                    const res = await request.get('/wecom-webhook/config');
                    const data = res.data.data || {};
                    configForm.value = {
                        webhook_url: data.webhook_url || '', enabled: Number(data.enabled || 0),
                        timeout_seconds: Number(data.timeout_seconds || 10),
                        retry_times: Number(data.retry_times || 3),
                    };
                    loaded.config = true;
                } catch (error) {
                    MessagePlugin.error('加载插件配置失败');
                } finally { configLoading.value = false; }
            };

            const saveConfig = async () => {
                const url = configForm.value.webhook_url.trim();
                if (url && !url.startsWith('https://')) {
                    MessagePlugin.warning('Webhook 地址必须使用 HTTPS');
                    return;
                }
                configSaving.value = true;
                try {
                    await request.put('/wecom-webhook/config', { ...configForm.value, webhook_url: url });
                    MessagePlugin.success('配置已保存');
                    await fetchConfig(true);
                } catch (error) {
                    MessagePlugin.error(error.response?.data?.detail || '保存失败');
                } finally { configSaving.value = false; }
            };

            const actionData = ref([]);
            const actionCategories = ref([]);
            const actionLoading = ref(false);
            const actionSaving = ref(false);
            const actionToggleKey = ref('');
            const actionTypeFilter = ref('');
            const actionAdvanced = ref([]);
            const actionDialogVisible = ref(false);
            const actionForm = ref({
                action_key: '', action_name: '', action_type: 'other', webhook_url: '',
                enabled: 0, available_variables: [],
            });
            const actionColumns = [
                { colKey: 'action_name', title: '动作名称', width: 180, cell: 'action_name' },
                { colKey: 'action_key', title: '动作标识', width: 200, ellipsis: true },
                { colKey: 'action_type', title: '分类', width: 100, cell: 'action_type' },
                { colKey: 'enabled', title: '状态', width: 88, align: 'center', cell: 'enabled' },
                { colKey: 'available_variables', title: '卡片字段', ellipsis: true, cell: 'available_variables' },
                { colKey: 'operation', title: '操作', width: 72, align: 'center', cell: 'operation' },
            ];
            const filteredActionData = computed(() => !actionTypeFilter.value
                ? actionData.value
                : actionData.value.filter(item => item.action_type === actionTypeFilter.value));

            const fetchActions = async (force) => {
                if (loaded.actions && !force) return;
                actionLoading.value = true;
                try {
                    const res = await request.get('/wecom-webhook/actions');
                    actionData.value = res.data.data?.list || [];
                    actionCategories.value = res.data.data?.categories || [];
                    loaded.actions = true;
                } catch (error) {
                    MessagePlugin.error('加载管理员通知动作失败');
                } finally { actionLoading.value = false; }
            };

            const openActionDialog = (row) => {
                actionForm.value = {
                    action_key: row.action_key, action_name: row.action_name,
                    action_type: row.action_type, webhook_url: row.webhook_url || '',
                    enabled: Number(row.enabled || 0), available_variables: row.available_variables || [],
                };
                actionAdvanced.value = [];
                actionDialogVisible.value = true;
            };

            const saveAction = async () => {
                const form = actionForm.value;
                const webhookUrl = form.webhook_url.trim();
                if (webhookUrl && !webhookUrl.startsWith('https://')) {
                    MessagePlugin.warning('动作级 Webhook 必须使用 HTTPS');
                    return;
                }
                actionSaving.value = true;
                try {
                    await request.put('/wecom-webhook/actions/' + encodeURIComponent(form.action_key), {
                        enabled: Number(form.enabled || 0), webhook_url: webhookUrl,
                    });
                    MessagePlugin.success('动作配置已保存');
                    actionDialogVisible.value = false;
                    await fetchActions(true);
                } catch (error) {
                    MessagePlugin.error(error.response?.data?.detail || '保存失败');
                } finally { actionSaving.value = false; }
            };

            const toggleAction = async (row, value) => {
                const enabled = Number(value || 0);
                actionToggleKey.value = row.action_key;
                try {
                    await request.put('/wecom-webhook/actions/' + encodeURIComponent(row.action_key), {
                        enabled, webhook_url: row.webhook_url || '',
                    });
                    row.enabled = enabled;
                    MessagePlugin.success(row.action_name + (enabled ? ' 已启用' : ' 已停用'));
                } catch (error) {
                    row.enabled = enabled ? 0 : 1;
                    MessagePlugin.error(error.response?.data?.detail || '状态更新失败');
                } finally { actionToggleKey.value = ''; }
            };

            const actionTypeLabel = (value) => value || '其他';
            const variablePreview = (items) => (items || []).map(item => item.desc).join('、') || '-';

            const emptyCardField = () => ({ keyname: '', value: '' });
            const sendForm = ref({
                main_title: '', main_desc: '', source_desc: '慧眼护农系统通知',
                sub_title_text: '', card_url: '', fields: Array.from({ length: 4 }, emptyCardField),
            });
            const sendSending = ref(false);
            const buildSendPayload = () => {
                const form = sendForm.value;
                if (!form.main_title.trim()) throw new Error('请填写卡片主标题');
                const fields = form.fields
                    .filter(item => item.keyname.trim() && item.value.trim())
                    .map(item => ({ keyname: item.keyname.trim(), value: item.value.trim() }));
                const url = form.card_url.trim();
                if (url && !/^https?:\/\//.test(url)) throw new Error('卡片跳转地址必须使用 HTTP(S)');
                return {
                    msgtype: 'template_card',
                    template_card: {
                        card_type: 'text_notice', source_desc: form.source_desc.trim(),
                        main_title: form.main_title.trim(), main_desc: form.main_desc.trim(),
                        sub_title_text: form.sub_title_text.trim(), card_url: url,
                        horizontal_content: fields,
                    },
                };
            };

            const sendMessage = async () => {
                let payload;
                try { payload = buildSendPayload(); }
                catch (error) { MessagePlugin.warning(error.message); return; }
                sendSending.value = true;
                try {
                    await request.post('/wecom-webhook/test', payload);
                    MessagePlugin.success('发送成功');
                    activeTab.value = 'logs';
                    await fetchLogs(true);
                } catch (error) {
                    MessagePlugin.error(error.response?.data?.detail || '发送失败');
                } finally { sendSending.value = false; }
            };

            const logData = ref([]);
            const logLoading = ref(false);
            const logFilters = reactive({ action_key: '', status: undefined });
            const logPagination = reactive({ current: 1, pageSize: 20, total: 0 });
            const logColumns = [
                { colKey: 'id', title: 'ID', width: 70 },
                { colKey: 'action_key', title: '动作标识', width: 180, ellipsis: true },
                { colKey: 'msgtype', title: '消息类型', width: 110 },
                { colKey: 'content', title: '卡片内容', ellipsis: true },
                { colKey: 'status', title: '状态', width: 80, cell: 'status' },
                { colKey: 'error_msg', title: '错误信息', width: 200, ellipsis: true },
                { colKey: 'create_time', title: '发送时间', width: 170 },
            ];

            const fetchLogs = async (force) => {
                if (loaded.logs && !force) return;
                logLoading.value = true;
                try {
                    const params = { page: logPagination.current, page_size: logPagination.pageSize };
                    if (logFilters.action_key) params.action_key = logFilters.action_key;
                    if (logFilters.status !== undefined && logFilters.status !== '') params.status = logFilters.status;
                    const res = await request.get('/wecom-webhook/logs', { params });
                    logData.value = res.data.data?.list || [];
                    logPagination.total = Number(res.data.data?.total || 0);
                    loaded.logs = true;
                } catch (error) {
                    MessagePlugin.error('加载发送日志失败');
                } finally { logLoading.value = false; }
            };
            const resetLogFilters = async () => {
                logFilters.action_key = ''; logFilters.status = undefined; logPagination.current = 1;
                await fetchLogs(true);
            };
            const onLogPageChange = async (pageInfo) => {
                logPagination.current = pageInfo.current; logPagination.pageSize = pageInfo.pageSize;
                await fetchLogs(true);
            };

            watch(activeTab, async (value) => {
                HuiYan.syncUrlTab(value);
                if (value === 'config') await fetchConfig(false);
                if (value === 'actions') await fetchActions(false);
                if (value === 'logs') { await fetchActions(false); await fetchLogs(false); }
            }, { immediate: true });

            return {
                activeTab, configForm, configLoading, configSaving, configSkeleton, saveConfig,
                actionData, actionCategories, filteredActionData, actionLoading, actionSaving, actionColumns,
                actionTypeFilter, actionToggleKey, toggleAction, actionAdvanced, actionDialogVisible,
                actionForm, openActionDialog, saveAction, actionTypeLabel, variablePreview,
                sendForm, sendSending, sendMessage, logData, logLoading, logFilters, logPagination,
                logColumns, fetchLogs, resetLogFilters, onLogPageChange,
            };
        },
    });
})();
