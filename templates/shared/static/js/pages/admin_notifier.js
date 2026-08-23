/**
 * 邮件通知管理员插件 — 前端页面逻辑
 *
 * 仅提供告警配置管理（通知开关、渠道、接口、接收人）和测试通知功能。
 * 任务日志、状态概览等已移至核心 task_monitor 页面。
 */
(function () {
    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'admin_notifier', page: 'admin_notifier',
        setup() {
            // ========== 告警配置 ==========
            const configLoading = ref(false);
            const configData = ref([]);
            const configSaving = ref(false);
            const configColumns = [
                { colKey: 'task_name', title: '任务标识', width: 160, ellipsis: true },
                { colKey: 'task_title', title: '显示名称', width: 140 },
                { colKey: 'task_type', title: '类型', width: 80 },
                { colKey: 'notify_enabled', title: '通知', width: 80, cell: 'notify_enabled' },
                { colKey: 'notify_interface', title: '接口', width: 150, cell: 'notify_interface' },
                { colKey: 'admin_ids', title: '接收管理员', width: 210, cell: 'admin_ids' },
            ];

            // 管理员列表和插件列表
            const adminList = ref([]);
            const notifyPluginList = ref([]);

            // 测试通知弹窗
            const testDialogVisible = ref(false);
            const testSending = ref(false);
            const testFormRef = ref(null);
            const testForm = ref({
                notify_interface: '',
                _admin_id_arr: [],
            });
            // 表单内联校验（视觉规范 9.9）：必填提示显示在输入框下方，禁止 MessagePlugin 弹出
            const testRules = {
                _admin_id_arr: [{ required: true, message: '请选择接收管理员', type: 'error' }],
            };

            // ========== 告警配置 ==========
            const fetchConfigs = async () => {
                configLoading.value = true;
                try {
                    var res = await request.get('/admin-notifier/configs');
                    if (res.status === 200) {
                        configData.value = (res.data.data.list || []).map(function (c) {
                            return Object.assign({}, c, {
                                _admin_id_arr: c.admin_ids ?
                                    c.admin_ids.split(',').filter(Boolean).map(Number) : [],
                                _changed: false,
                            });
                        });
                    }
                } catch (e) {
                    MessagePlugin.error('加载配置失败');
                } finally {
                    configLoading.value = false;
                }
            };

            const markConfigChanged = (row) => {
                row._changed = true;
            };

            const onAdminIdsChange = (row) => {
                row.admin_ids = (row._admin_id_arr || []).join(',');
                row._changed = true;
            };

            const saveAllConfigs = async () => {
                var changed = configData.value.filter(function (c) { return c._changed; });
                if (changed.length === 0) {
                    MessagePlugin.info('没有需要保存的更改');
                    return;
                }
                configSaving.value = true;
                try {
                    var items = changed.map(function (c) {
                        return {
                            task_name: c.task_name,
                            task_title: c.task_title,
                            task_type: c.task_type,
                            notify_enabled: c.notify_enabled,
                            notify_interface: c.notify_interface || '',
                            admin_ids: c.admin_ids || '',
                        };
                    });
                    await request.put('/admin-notifier/configs', { items: items });
                    MessagePlugin.success('保存成功');
                    fetchConfigs();
                } catch (e) {
                    MessagePlugin.error('保存失败');
                } finally {
                    configSaving.value = false;
                }
            };

            // ========== 管理员列表 & 插件列表 ==========
            const fetchAdmins = async () => {
                try {
                    var res = await request.get('/admin-notifier/admins');
                    if (res.status === 200) {
                        adminList.value = res.data.data.list;
                    }
                } catch (e) { /* 静默 */ }
            };

            const fetchNotifyPlugins = async () => {
                try {
                    var res = await request.get('/admin-notifier/plugins');
                    if (res.status === 200) {
                        notifyPluginList.value = res.data.data.list;
                    }
                } catch (e) { /* 静默 */ }
            };

            // ========== 测试通知 ==========
            const openTestDialog = () => {
                testForm.value = {
                    notify_interface: '',
                    _admin_id_arr: [],
                };
                testDialogVisible.value = true;
            };

            const sendTestNotify = async () => {
                if (!testFormRef.value) return;
                // 内联校验：未通过时错误已显示在对应输入框下方，直接中断
                var valid = await testFormRef.value.validate();
                if (valid !== true) return;
                testSending.value = true;
                try {
                    await request.post('/admin-notifier/test', {
                        notify_interface: testForm.value.notify_interface || '',
                        admin_ids: testForm.value._admin_id_arr.join(','),
                    });
                    MessagePlugin.success('测试通知已发送');
                    testDialogVisible.value = false;
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '发送失败');
                } finally {
                    testSending.value = false;
                }
            };

            onMounted(() => {
                fetchConfigs();
                fetchAdmins();
                fetchNotifyPlugins();
            });

            return {
                configLoading, configData, configSaving, configColumns,
                adminList, notifyPluginList,
                testDialogVisible, testSending, testFormRef, testForm, testRules,
                fetchConfigs, markConfigChanged, onAdminIdsChange, saveAllConfigs,
                fetchAdmins, fetchNotifyPlugins,
                openTestDialog, sendTestNotify,
            };
        },
    });
})();
