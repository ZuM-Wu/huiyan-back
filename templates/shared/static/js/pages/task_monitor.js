/** 任务执行历史、队列、死信与平台目录管理页。 */
(function () {
    const { ref, reactive, onMounted, watch } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            // 初始值从 URL ?tab= 读取，切换时写回 URL。
            const allTabs = ['logs', 'queue', 'dead', 'events', 'capabilities', 'outbox'];
            const activeTab = ref(HuiYan.getUrlTab('logs', allTabs));
            const platform = HuiYanTaskPlatform.create(request);

            // ===== 执行日志 =====
            const logLoading = ref(false);
            const logData = ref([]);
            const logFilter = reactive({
                task_type: '', status: '', handle_status: '', keyword: '', dateRange: [],
            });
            const logPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const logColumns = [
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'task_desc', title: '任务描述', minWidth: 160, cell: 'task_desc' },
                { colKey: 'task_type', title: '类型', width: 80, cell: 'task_type' },
                { colKey: 'duration_ms', title: '耗时', width: 80, cell: 'duration_ms' },
                { colKey: 'start_time', title: '开始时间', width: 160, ellipsis: true },
                { colKey: 'end_time', title: '结束时间', width: 160, ellipsis: true },
                { colKey: 'handle_status', title: '处理状态', width: 90, cell: 'handle_status' },
                { colKey: 'op', title: '操作', width: 220, cell: 'op', fixed: 'right' },
            ];
            const logDetailVisible = ref(false);
            const logDetailData = ref(null);
            const handleDialogVisible = ref(false);
            const handleDialogTitle = ref('标记已处理');
            const handleNote = ref('');
            const handleAction = ref('');
            const handleLogId = ref(0);
            const cleanupDialogVisible = ref(false);
            const cleanupLoading = ref(false);
            const cleanupRetentionDays = ref(30);

            // ===== 任务队列 =====
            const queueLoading = ref(false);
            const queueData = ref([]);
            const queueFilter = reactive({ type: '', status: '', keyword: '' });
            const queuePagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const queueColumns = [
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'type', title: '类型', width: 130, cell: 'type' },
                { colKey: 'description', title: '描述', minWidth: 160, ellipsis: true },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'attempt', title: '尝试', width: 80, cell: 'retry' },
                { colKey: 'next_run_at', title: '下次执行', width: 160, ellipsis: true },
                { colKey: 'create_time', title: '创建时间', width: 160, ellipsis: true },
                { colKey: 'start_time', title: '执行时间', width: 160, ellipsis: true },
                { colKey: 'op', title: '操作', width: 180, cell: 'op', fixed: 'right' },
            ];
            const queueDetailVisible = ref(false);
            const queueDetailData = ref(null);

            // ===== 失败任务 Tab =====
            const failedLoading = ref(false);
            const failedData = ref([]);
            const failedFilter = reactive({ keyword: '' });
            const failedPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const failedColumns = [
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'type', title: '类型', width: 130, cell: 'type' },
                { colKey: 'description', title: '描述', minWidth: 160, ellipsis: true },
                { colKey: 'attempt', title: '尝试', width: 80, cell: 'retry' },
                { colKey: 'next_run_at', title: '下次执行', width: 160, ellipsis: true },
                { colKey: 'create_time', title: '创建时间', width: 160, ellipsis: true },
                { colKey: 'start_time', title: '执行时间', width: 160, ellipsis: true },
                { colKey: 'op', title: '操作', width: 180, cell: 'op', fixed: 'right' },
            ];

            // ===== 队列配置弹窗 =====
            const configDialogVisible = ref(false);
            const configLoading = ref(false);
            const configForm = ref({
                task_queue_enabled: 1,
                task_queue_poll_interval: 3,
                task_queue_batch_size: 10,
                task_queue_clean_finish: 1,
            });

            // ===== 通用工具 =====
            const taskTypeLabel = (type) => {
                return { system: '系统', weather: '天气', notice: '通知', plugin: '插件' }[type] || type || '-';
            };

            const queueTypeLabel = (type) => {
                const map = {
                    notice: '通知',
                    push_execute: '推送执行',
                    clean_repeat_cache: '清理防重复缓存',
                    clean_old_logs: '清理过期系统日志',
                    clean_task_logs: '清理过期任务日志',
                    sweep_expired_cache: '清扫过期缓存',
                    weather_pull: '天气全量拉取',
                    weather_daily_finalize: '天气日终定格',
                    weather_clean: '天气数据清理',
                    weather_alert_notify: '天气预警补推',
                };
                return map[type] || type || '-';
            };

            const formatDuration = (ms) => {
                if (!ms) return '-';
                if (ms < 1000) return ms + 'ms';
                return (ms / 1000).toFixed(1) + 's';
            };

            const onTabChange = (tab) => {
                if (activeTab.value !== tab) {
                    activeTab.value = tab;
                }
            };

            watch(activeTab, (tab) => {
                HuiYan.syncUrlTab(tab);
                loadTab(tab);
            });

            const loadTab = (tab) => {
                if (tab === 'logs') fetchLogs();
                else if (tab === 'queue') fetchQueueList();
                else if (tab === 'dead') fetchFailedList();
                else if (tab === 'events') platform.fetchEvents();
                else if (tab === 'capabilities') {
                    platform.fetchEvents();
                    platform.fetchDefinitions();
                } else if (tab === 'outbox') platform.fetchOutbox();
            };

            // ===== 执行日志 =====
            const fetchLogs = async () => {
                logLoading.value = true;
                try {
                    var params = {
                        task_type: logFilter.task_type,
                        status: logFilter.status,
                        handle_status: logFilter.handle_status,
                        keyword: logFilter.keyword,
                        page: logPagination.current,
                        limit: logPagination.pageSize,
                    };
                    if (logFilter.dateRange && logFilter.dateRange.length === 2) {
                        params.start_date = logFilter.dateRange[0];
                        params.end_date = logFilter.dateRange[1];
                    }
                    var res = await request.get('/task-monitor/logs', { params: params });
                    if (res.status === 200) {
                        logData.value = res.data.data.list;
                        logPagination.total = res.data.data.total;
                    }
                } catch (e) {
                    MessagePlugin.error('加载日志失败');
                } finally {
                    logLoading.value = false;
                }
            };

            const onLogPageChange = (info) => {
                logPagination.current = info.current;
                logPagination.pageSize = info.pageSize;
                fetchLogs();
            };

            const viewLogDetail = async (row) => {
                logDetailData.value = null;
                logDetailVisible.value = true;
                try {
                    var res = await request.get('/task-monitor/logs/' + row.id);
                    if (res.status === 200) {
                        logDetailData.value = res.data.data;
                    }
                } catch (e) {
                    MessagePlugin.error('加载详情失败');
                }
            };

            const retryTask = (row) => {
                DialogPlugin.confirm({
                    header: '确认重试',
                    body: '确认重试任务「' + row.task_desc + '」？将立即执行一次该任务。',
                    onConfirm: async () => {
                        try {
                            await request.post('/task-monitor/logs/' + row.id + '/retry');
                            MessagePlugin.success('重试已执行');
                            fetchLogs();
                        } catch (e) {
                            MessagePlugin.error(e.response?.data?.detail || '重试失败');
                        }
                    },
                });
            };

            const handleTask = (row) => {
                handleDialogTitle.value = '标记已处理';
                handleAction.value = 'handle';
                handleLogId.value = row.id;
                handleNote.value = '';
                handleDialogVisible.value = true;
            };

            const ignoreTask = (row) => {
                handleDialogTitle.value = '标记已忽略';
                handleAction.value = 'ignore';
                handleLogId.value = row.id;
                handleNote.value = '';
                handleDialogVisible.value = true;
            };

            const confirmHandle = async () => {
                try {
                    var url = '/task-monitor/logs/' + handleLogId.value + '/' +
                        (handleAction.value === 'handle' ? 'handle' : 'ignore');
                    await request.post(url, { note: handleNote.value });
                    MessagePlugin.success('操作成功');
                    handleDialogVisible.value = false;
                    fetchLogs();
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '操作失败');
                }
            };

            // ===== 任务队列 =====
            const fetchQueueList = async () => {
                queueLoading.value = true;
                try {
                    var params = {
                        task_type: queueFilter.type,
                        status: queueFilter.status,
                        keyword: queueFilter.keyword,
                        page: queuePagination.current,
                        limit: queuePagination.pageSize,
                    };
                    var res = await request.get('/task-queue/list', { params: params });
                    if (res.status === 200) {
                        queueData.value = res.data.data.list;
                        queuePagination.total = res.data.data.total;
                    }
                } catch (e) {
                    MessagePlugin.error('加载队列失败');
                } finally {
                    queueLoading.value = false;
                }
            };

            const onQueuePageChange = (info) => {
                queuePagination.current = info.current;
                queuePagination.pageSize = info.pageSize;
                fetchQueueList();
            };

            const viewQueueDetail = async (row) => {
                queueDetailData.value = null;
                queueDetailVisible.value = true;
                try {
                    var res = await request.get('/task-queue/' + row.id);
                    if (res.status === 200) {
                        queueDetailData.value = res.data.data;
                    }
                } catch (e) {
                    MessagePlugin.error('加载详情失败');
                }
            };

            const retryQueueTask = (row, source) => {
                DialogPlugin.confirm({
                    header: '确认重试',
                    body: '确认重试队列任务 #' + row.id + '？状态将重置为等待中。',
                    onConfirm: async () => {
                        try {
                            await request.post('/task-queue/' + row.id + '/retry');
                            MessagePlugin.success('任务已重置为待执行');
                            if (source === 'dead') {
                                fetchFailedList();
                            } else {
                                fetchQueueList();
                            }
                        } catch (e) {
                            MessagePlugin.error(e.response?.data?.detail || '重试失败');
                        }
                    },
                });
            };

            const cancelQueueTask = (row, source) => {
                DialogPlugin.confirm({
                    header: '确认取消',
                    body: '确认取消队列任务 #' + row.id + '？任务记录与执行历史将保留。',
                    onConfirm: async () => {
                        try {
                            await request.post('/task-queue/' + row.id + '/cancel');
                            MessagePlugin.success('任务已取消');
                            if (source === 'dead') {
                                fetchFailedList();
                            } else {
                                fetchQueueList();
                            }
                        } catch (e) {
                            MessagePlugin.error(e.response?.data?.detail || '删除失败');
                        }
                    },
                });
            };

            const openCleanupDialog = () => {
                cleanupRetentionDays.value = 30;
                cleanupDialogVisible.value = true;
            };

            const cleanupLogs = () => {
                if (cleanupLoading.value) return;
                let confirmDialog;
                const closeConfirmDialog = () => {
                    if (confirmDialog) {
                        confirmDialog.destroy();
                        confirmDialog = null;
                    }
                };
                confirmDialog = DialogPlugin.confirm({
                    header: '确认清理日志',
                    body: '将删除 ' + cleanupRetentionDays.value + ' 天以前的任务执行日志，且无法恢复，是否继续？',
                    onConfirm: async () => {
                        closeConfirmDialog();
                        cleanupLoading.value = true;
                        try {
                            const res = await request.delete('/task-monitor/logs/cleanup', {
                                params: { retention_days: cleanupRetentionDays.value },
                            });
                            MessagePlugin.success('已清理 ' + res.data.data.deleted_count + ' 条任务日志');
                            cleanupDialogVisible.value = false;
                            fetchLogs();
                        } catch (e) {
                            MessagePlugin.error(e.response?.data?.detail || '清理日志失败');
                        } finally {
                            closeConfirmDialog();
                            cleanupLoading.value = false;
                        }
                    },
                    onCancel: closeConfirmDialog,
                });
            };

            const resumeQueueTask = async (row) => {
                try {
                    await request.post('/task-queue/' + row.id + '/resume');
                    MessagePlugin.success('任务已恢复');
                    fetchQueueList();
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '恢复失败');
                }
            };

            // ===== 失败任务 =====
            const fetchFailedList = async () => {
                failedLoading.value = true;
                try {
                    var params = {
                        status: 'Dead',
                        keyword: failedFilter.keyword,
                        page: failedPagination.current,
                        limit: failedPagination.pageSize,
                    };
                    var res = await request.get('/task-queue/list', { params: params });
                    if (res.status === 200) {
                        failedData.value = res.data.data.list;
                        failedPagination.total = res.data.data.total;
                    }
                } catch (e) {
                    MessagePlugin.error('加载失败任务失败');
                } finally {
                    failedLoading.value = false;
                }
            };

            const onFailedPageChange = (info) => {
                failedPagination.current = info.current;
                failedPagination.pageSize = info.pageSize;
                fetchFailedList();
            };

            // ===== 队列配置 =====
            const openConfigDialog = async () => {
                configDialogVisible.value = true;
                await fetchConfig();
            };

            const fetchConfig = async () => {
                configLoading.value = true;
                try {
                    var res = await request.get('/task-queue/config');
                    if (res.status === 200) {
                        configForm.value = res.data.data;
                    }
                } catch (e) {
                    MessagePlugin.error('加载配置失败');
                } finally {
                    configLoading.value = false;
                }
            };

            const saveConfig = async () => {
                configLoading.value = true;
                try {
                    await request.put('/task-queue/config', configForm.value);
                    MessagePlugin.success('配置已保存');
                    configDialogVisible.value = false;
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '保存失败');
                } finally {
                    configLoading.value = false;
                }
            };

            onMounted(() => {
                platform.fetchDefinitions();
                loadTab(activeTab.value);
            });

            return {
                activeTab, ...platform,
                logLoading, logData, logFilter, logPagination, logColumns,
                logDetailVisible, logDetailData,
                handleDialogVisible, handleDialogTitle, handleNote, handleAction, handleLogId,
                cleanupDialogVisible, cleanupLoading, cleanupRetentionDays,
                queueLoading, queueData, queueFilter, queuePagination, queueColumns,
                queueDetailVisible, queueDetailData,
                failedLoading, failedData, failedFilter, failedPagination, failedColumns,
                configDialogVisible, configLoading, configForm,
                taskTypeLabel, queueTypeLabel, formatDuration, onTabChange,
                fetchLogs, onLogPageChange, viewLogDetail, retryTask,
                openCleanupDialog, cleanupLogs,
                handleTask, ignoreTask, confirmHandle,
                fetchQueueList, onQueuePageChange, viewQueueDetail,
                retryQueueTask, cancelQueueTask, resumeQueueTask,
                fetchFailedList, onFailedPageChange,
                openConfigDialog, fetchConfig, saveConfig,
            };
        },
    });
})();
