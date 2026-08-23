/**
 * 发送设置页 — 统一管理短信+邮件通知动作绑定
 *
 * 对标 ZJMF 发送设置：
 * - 合并国内短信、国际短信、邮件三通道到同一表格
 * - 支持按动作类型筛选 + 关键词搜索
 * - 行内编辑（select/switch）+ 防抖自动保存
 * - 底部批量保存按钮
 */
(function () {
HuiYan.createPage({
    setup() {
        const { ref, reactive, onMounted, computed, watch } = Vue;
        const { MessagePlugin } = TDesign;

        // ============================================================
        // 数据源
        // ============================================================
        const loading = ref(false);
        const actionList = ref([]);
        const searchKeyword = ref('');
        // 分类 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
        const activeType = ref(HuiYan.getUrlTab('', ['', 'user', 'production', 'order', 'other']));
        watch(activeType, function (val) { HuiYan.syncUrlTab(val); });

        // 短信接口列表
        const smsPluginList = ref([]);
        // 邮件接口列表
        const emailPluginList = ref([]);
        // 短信模板列表
        const allSmsTemplates = ref([]);
        // 邮件模板列表
        const allEmailTemplates = ref([]);

        // 表格列定义
        const columns = [
            { colKey: 'action_name', title: '动作名称', minWidth: 120 },
            { colKey: 'action_type', title: '类型', width: 100, cell: 'action_type' },
            { colKey: 'sms_interface', title: '短信接口', width: 170 },
            { colKey: 'sms_template_id', title: '短信模板', width: 190 },
            { colKey: 'sms_enabled', title: '短信启用', width: 90 },
            { colKey: 'email_interface', title: '邮件接口', width: 170 },
            { colKey: 'email_template_id', title: '邮件模板', width: 190 },
            { colKey: 'email_enabled', title: '邮件启用', width: 90 },
        ];

        // 按类型+关键词筛选后的数据
        const filteredActions = computed(function () {
            var list = actionList.value;
            if (activeType.value) {
                var typeMap = {
                    production: ['area'],
                    other: ['system', 'weather'],
                };
                var actionTypes = typeMap[activeType.value] || [activeType.value];
                list = list.filter(function (a) {
                    return actionTypes.indexOf(a.action_type) >= 0;
                });
            }
            if (searchKeyword.value) {
                var kw = searchKeyword.value.toLowerCase();
                list = list.filter(function (a) {
                    return (a.action_name || '').toLowerCase().indexOf(kw) >= 0 ||
                           (a.action_key || '').toLowerCase().indexOf(kw) >= 0;
                });
            }
            return list;
        });

        // ============================================================
        // 数据加载
        // ============================================================
        async function fetchActions() {
            loading.value = true;
            try {
                const res = await request.get('/notice/actions/list', { params: { page: 1, limit: 100 } });
                actionList.value = (res.data.data || res.data).list || [];
            } finally {
                loading.value = false;
            }
        }

        async function fetchSmsPlugins() {
            try {
                const res = await request.get('/notice/interfaces/list', { params: { module: 'sms' } });
                smsPluginList.value = ((res.data.data || res.data).list || []).filter(function (p) { return p.installed; });
            } catch (e) { /* 静默 */ }
        }

        async function fetchEmailPlugins() {
            try {
                const res = await request.get('/notice/interfaces/list', { params: { module: 'mail' } });
                emailPluginList.value = ((res.data.data || res.data).list || []).filter(function (p) { return p.installed; });
            } catch (e) { /* 静默 */ }
        }

        async function fetchSmsTemplates() {
            try {
                const res = await request.get('/notice/sms-templates/list', { params: { page: 1, limit: 100 } });
                allSmsTemplates.value = (res.data.data || res.data).list || [];
            } catch (e) { /* 静默 */ }
        }

        async function fetchEmailTemplates() {
            try {
                const res = await request.get('/notice/email-templates/list', { params: { page: 1, limit: 100 } });
                allEmailTemplates.value = (res.data.data || res.data).list || [];
            } catch (e) { /* 静默 */ }
        }

        // 类型标签
        function typeLabel(type) {
            return {
                user: '用户账户',
                order: '订单',
                area: '产区相关',
                system: '系统',
                weather: '天气预警',
            }[type] || type || '-';
        }

        // 按接口筛选短信模板（短信需平台审核，模板与接口绑定）
        function filterSmsTemplates(iface) {
            if (!iface) return allSmsTemplates.value;
            return allSmsTemplates.value.filter(function (t) { return t.interface === iface; });
        }

        // ============================================================
        // 行内编辑 - 防抖保存
        // ============================================================
        var saveTimers = {};
        function saveRow(row) {
            var key = row.action_key;
            if (saveTimers[key]) clearTimeout(saveTimers[key]);
            saveTimers[key] = setTimeout(function () {
                request.put('/notice/actions/' + key, {
                    sms_enabled: row.sms_enabled,
                    sms_interface: row.sms_interface || '',
                    sms_template_id: row.sms_template_id || 0,
                    email_enabled: row.email_enabled,
                    email_interface: row.email_interface || '',
                    email_template_id: row.email_template_id || 0,
                }).then(function () {
                    MessagePlugin.success(row.action_name + ' 已保存');
                }).catch(function () {
                    MessagePlugin.error(row.action_name + ' 保存失败');
                });
                delete saveTimers[key];
            }, 600);
        }

        // ============================================================
        // 批量保存
        // ============================================================
        const batchSaving = ref(false);

        async function batchSave() {
            batchSaving.value = true;
            var errors = 0;
            try {
                var promises = actionList.value.map(function (row) {
                    return request.put('/notice/actions/' + row.action_key, {
                        sms_enabled: row.sms_enabled,
                        sms_interface: row.sms_interface || '',
                        sms_template_id: row.sms_template_id || 0,
                        email_enabled: row.email_enabled,
                        email_interface: row.email_interface || '',
                        email_template_id: row.email_template_id || 0,
                    }).catch(function () { errors++; });
                });
                await Promise.all(promises);
                if (errors === 0) {
                    MessagePlugin.success('全部保存成功');
                } else {
                    MessagePlugin.warning('保存完成，' + errors + ' 条失败');
                }
            } finally {
                batchSaving.value = false;
            }
        }

        // ============================================================
        // 批量配置接口
        // ============================================================
        const batchDialogVisible = ref(false);
        const batchForm = reactive({
            sms_interface: '',
            email_interface: '',
        });

        function openBatchConfig() {
            batchForm.sms_interface = '';
            batchForm.email_interface = '';
            batchDialogVisible.value = true;
        }

        async function applyBatchConfig() {
            var count = 0;
            actionList.value.forEach(function (row) {
                if (batchForm.sms_interface) {
                    row.sms_interface = batchForm.sms_interface;
                    count++;
                }
                if (batchForm.email_interface) {
                    row.email_interface = batchForm.email_interface;
                    count++;
                }
            });
            batchDialogVisible.value = false;
            if (count > 0) {
                MessagePlugin.success('已批量设置接口，请点击底部保存按钮提交');
            }
        }

        // ============================================================
        // 初始化
        // ============================================================
        onMounted(function () {
            fetchActions();
            fetchSmsPlugins();
            fetchEmailPlugins();
            fetchSmsTemplates();
            fetchEmailTemplates();
        });

        return {
            loading, columns, searchKeyword, activeType,
            filteredActions, fetchActions,
            smsPluginList, emailPluginList,
            allEmailTemplates,
            filterSmsTemplates,
            typeLabel,
            saveRow,
            batchSaving, batchSave,
            batchDialogVisible, batchForm, openBatchConfig, applyBatchConfig,
        };
    },
});
})();
