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
            // 队列、死信与队列配置独立成分区文件，页面按 DOM 顺序串行加载后组合。
            const queueSection = HuiYanTaskMonitorQueue.create(request);

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
            // 手动清理默认删除最近 10 天日志，与后端默认值保持一致
            const cleanupDefaultDays = 10;
            const cleanupDays = ref(cleanupDefaultDays);
            // 清理窗口内的日志条数，仅用于确认弹窗提示影响范围
            const cleanupPreviewCount = ref(null);

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
                else if (tab === 'queue') queueSection.fetchQueueList();
                else if (tab === 'dead') queueSection.fetchFailedList();
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

            // 最近 N 个自然日的窗口起点日期（含今天，与后端口径一致）
            const cleanupStartDate = () => {
                const start = new Date();
                start.setDate(start.getDate() - (Number(cleanupDays.value) - 1));
                const month = String(start.getMonth() + 1).padStart(2, '0');
                const day = String(start.getDate()).padStart(2, '0');
                return start.getFullYear() + '-' + month + '-' + day;
            };

            // 查询窗口内日志条数；失败时只是不展示条数，不阻断清理流程
            const fetchCleanupPreview = async () => {
                try {
                    const res = await request.get('/task-monitor/logs', {
                        params: { start_date: cleanupStartDate(), page: 1, limit: 1 },
                    });
                    cleanupPreviewCount.value = res.data.data.total;
                } catch (e) {
                    cleanupPreviewCount.value = null;
                }
            };

            const openCleanupDialog = () => {
                cleanupDays.value = cleanupDefaultDays;
                cleanupPreviewCount.value = null;
                cleanupDialogVisible.value = true;
                fetchCleanupPreview();
            };

            const cleanupLogs = () => {
                if (cleanupLoading.value) return;
                const countText = cleanupPreviewCount.value === null
                    ? '' : '，共 ' + cleanupPreviewCount.value + ' 条';
                let confirmDialog;
                const closeConfirmDialog = () => {
                    if (confirmDialog) {
                        confirmDialog.destroy();
                        confirmDialog = null;
                    }
                };
                confirmDialog = DialogPlugin.confirm({
                    header: '确认清理日志',
                    body: '将删除 ' + cleanupStartDate() + ' 起最近 ' + cleanupDays.value
                        + ' 个自然日内的任务日志' + countText + '，更早的日志保留；'
                        + '此操作无法恢复，是否继续？',
                    onConfirm: async () => {
                        closeConfirmDialog();
                        cleanupLoading.value = true;
                        try {
                            const res = await request.delete('/task-monitor/logs/cleanup', {
                                params: { days: cleanupDays.value },
                            });
                            const data = res.data.data;
                            if (data.deleted_count === 0) {
                                MessagePlugin.warning('最近 ' + data.days
                                    + ' 天内没有可删除的任务日志');
                            } else {
                                MessagePlugin.success('已清理最近 ' + data.days + ' 天内的 '
                                    + data.deleted_count + ' 条任务日志');
                            }
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

            onMounted(() => {
                platform.fetchDefinitions();
                loadTab(activeTab.value);
            });

            return {
                activeTab, ...platform, ...queueSection,
                logLoading, logData, logFilter, logPagination, logColumns,
                logDetailVisible, logDetailData,
                handleDialogVisible, handleDialogTitle, handleNote, handleAction, handleLogId,
                cleanupDialogVisible, cleanupLoading, cleanupDays, cleanupPreviewCount,
                taskTypeLabel, queueTypeLabel, formatDuration, onTabChange,
                fetchLogs, onLogPageChange, viewLogDetail, retryTask,
                openCleanupDialog, cleanupLogs, fetchCleanupPreview,
                handleTask, ignoreTask, confirmHandle,
            };
        },
    });
})();
