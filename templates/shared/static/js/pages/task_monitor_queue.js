/**
 * 任务监控页的任务队列、失败任务与队列配置分区。
 *
 * 分区依据：单文件有效行上限 400，页面按「执行日志 / 队列与死信 / 队列配置」拆分，
 * 本文件由 task_monitor.js 组合；Vue、TDesign 与 request 运行时由页面壳统一提供。
 */
(function () {
    const { ref, reactive } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    function createQueueSection(request) {
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

        return {
            queueLoading, queueData, queueFilter, queuePagination, queueColumns,
            queueDetailVisible, queueDetailData,
            failedLoading, failedData, failedFilter, failedPagination, failedColumns,
            configDialogVisible, configLoading, configForm,
            fetchQueueList, onQueuePageChange, viewQueueDetail,
            retryQueueTask, cancelQueueTask, resumeQueueTask,
            fetchFailedList, onFailedPageChange,
            openConfigDialog, fetchConfig, saveConfig,
        };
    }

    window.HuiYanTaskMonitorQueue = { create: createQueueSection };
})();
