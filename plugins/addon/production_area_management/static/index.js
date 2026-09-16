/**
 * 产区管理插件管理端页面逻辑。
 * 页面只展示真实按日事实、整改建议和现场反馈，不再模拟 AI、MCP 或固定演示动作。
 */
(function () {
    const { ref, computed, reactive, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    const TAB_KEY = 'tab';
    const TABS = ['logs', 'tasks', 'schedule'];
    const PERMISSION = {
        list: 'production_area_management:list',
        log: 'production_area_management:log:generate',
        task: 'production_area_management:task:generate',
        feedback: 'production_area_management:feedback',
    };
    const emptyOverview = {
        readiness: { ready: false, message: '' },
        area: null,
        weather: null,
        gdd: null,
        latest_log_exists: false,
        latest_log_id: 0,
        log_count: 0,
        task_stats: { total: 0, pending: 0, completed: 0 },
    };

    HuiYan.createPluginPage({
        plugin: 'production_area_management',
        page: 'index',
        setup(context) {
            const api = context.api;
            const authCodes = HuiYan.getAuthCodes();
            const activeTab = ref(HuiYan.getUrlTab('logs', TABS));
            const initialLoading = ref(true);
            const errorMessage = ref('');
            const overview = ref(Object.assign({}, emptyOverview));
            const logs = ref([]);
            const tasks = ref([]);
            const taskFilter = ref('all');
            const logDialogVisible = ref(false);
            const logDetailLoading = ref(false);
            const logDetail = ref(null);
            const taskGenerating = ref(false);
            const taskDialogVisible = ref(false);
            const detailLoading = ref(false);
            const taskDetail = ref(null);
            const feedbackSubmitting = ref(false);
            const feedbackForm = reactive({ result: 'success', content: '', images: [] });
            const scheduleSaving = ref(false);
            const syncingNow = ref(false);
            const scheduleForm = reactive({ enabled: true, time: '06:00' });
            const scheduleInfo = reactive({ registered: false, next_run_time: '' });
            const lastSyncTaskId = ref(0);

            const taskColumns = [
                { colKey: 'title', title: '任务', width: 280, cell: 'title', ellipsis: true },
                { colKey: 'priority', title: '优先级', width: 80, cell: 'priority' },
                { colKey: 'assignee', title: '负责人', width: 100 },
                { colKey: 'plan_time', title: '计划时间', width: 150, cell: 'plan_time' },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'feedback', title: '现场反馈', width: 110, cell: 'feedback' },
                { colKey: 'operation', title: '操作', width: 80, cell: 'operation' },
            ];
            const hasPermission = (code) => authCodes.indexOf(code) !== -1;
            const filteredTasks = computed(() => taskFilter.value === 'all'
                ? tasks.value : tasks.value.filter((item) => item.status === taskFilter.value));
            const coordinateText = computed(() => {
                const area = overview.value.area;
                if (!area) return '未选择产区';
                const longitude = Number(area.longitude);
                const latitude = Number(area.latitude);
                if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return '未维护经纬度';
                return `东经 ${longitude.toFixed(3)} / 北纬 ${latitude.toFixed(3)}`;
            });
            const weatherSourceText = computed(() => {
                const weather = overview.value.weather;
                if (!weather) return '暂无天气快照';
                const source = weather.source_title || weather.source || '天气服务';
                const observed = weather.obs_time || weather.fetch_time || '时间未知';
                return `数据来源：${source} · 观测 ${observed}`;
            });
            const logWeatherText = computed(() => {
                const weather = logDetail.value && logDetail.value.log.weather;
                if (!weather || !Object.keys(weather).length) return '暂无逐日天气事实';
                return weather.text_day || '已记录天气事实';
            });
            const weatherValue = (key, fallback) => {
                const value = overview.value.weather && overview.value.weather[key];
                return value === undefined || value === null || value === '' ? fallback : value;
            };
            const gddValue = (key, fallback) => {
                const value = overview.value.gdd && overview.value.gdd[key];
                return value === undefined || value === null || value === '' ? fallback : value;
            };
            const formatDateTime = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '--';
            const priorityText = (value) => ({ high: '高', medium: '中', low: '低' }[value] || value || '中');
            const priorityTheme = (value) => ({ high: 'danger', medium: 'warning', low: 'default' }[value] || 'default');
            const statusText = (value) => ({ pending: '待处理', completed: '已完成' }[value] || value || '待处理');
            const statusTheme = (value) => value === 'completed' ? 'success' : 'primary';
            const resultText = (value) => ({ success: '完成', partial: '部分完成', failed: '未完成' }[value] || value);
            const resultTheme = (value) => ({ success: 'success', partial: 'warning', failed: 'danger' }[value] || 'default');
            const unpack = (response) => response && response.data ? (response.data.data || {}) : (response || {});

            const onTabChange = (value) => HuiYan.syncUrlTab(value);
            const loadOverview = async () => {
                const response = await api.get('/overview');
                overview.value = Object.assign({}, emptyOverview, unpack(response));
            };
            const loadLogs = async () => {
                const response = await api.get('/logs');
                logs.value = unpack(response).list || [];
            };
            const loadTasks = async () => {
                const response = await api.get('/tasks?status=all');
                tasks.value = unpack(response).list || [];
            };
            const loadSchedule = async () => {
                const response = await api.get('/schedule');
                const data = unpack(response);
                scheduleForm.enabled = data.enabled !== false;
                scheduleForm.time = data.time || '06:00';
                scheduleInfo.registered = !!data.registered;
                scheduleInfo.next_run_time = data.next_run_time || '';
            };
            const reloadAll = async () => {
                errorMessage.value = '';
                try {
                    await Promise.all([loadOverview(), loadLogs(), loadTasks(), loadSchedule()]);
                } catch (error) {
                    errorMessage.value = '产区事实页面加载失败，请检查后端和天气服务状态。';
                }
            };

            const openLog = async (log) => {
                logDialogVisible.value = true;
                logDetailLoading.value = true;
                logDetail.value = null;
                try {
                    logDetail.value = unpack(await api.get(`/logs/${log.id}`));
                } finally {
                    logDetailLoading.value = false;
                }
            };
            const generateTaskFromLog = async () => {
                if (!logDetail.value || !hasPermission(PERMISSION.task)) return;
                taskGenerating.value = true;
                try {
                    const data = unpack(await api.post(`/logs/${logDetail.value.log.id}/task`));
                    logDetail.value.task = data.task || null;
                    MessagePlugin.success(data.created ? '整改任务已生成' : '整改任务已存在');
                    await Promise.all([loadTasks(), loadOverview()]);
                } finally {
                    taskGenerating.value = false;
                }
            };
            const openTask = async (row) => {
                taskDialogVisible.value = true;
                detailLoading.value = true;
                taskDetail.value = null;
                feedbackForm.result = 'success';
                feedbackForm.content = '';
                feedbackForm.images = [];
                try {
                    taskDetail.value = unpack(await api.get(`/tasks/${row.id}`));
                } finally {
                    detailLoading.value = false;
                }
            };
            const addFeedbackImage = () => {
                if (feedbackForm.images.length < 9) feedbackForm.images.push('');
            };
            const removeFeedbackImage = (index) => feedbackForm.images.splice(index, 1);
            const submitFeedback = async () => {
                if (!taskDetail.value || !hasPermission(PERMISSION.feedback)) return;
                const content = String(feedbackForm.content || '').trim();
                if (content.length < 5) {
                    MessagePlugin.warning('请填写至少 5 个字的现场说明');
                    return;
                }
                feedbackSubmitting.value = true;
                try {
                    await api.post(`/tasks/${taskDetail.value.task.id}/feedback`, {
                        result: feedbackForm.result,
                        content,
                        images: feedbackForm.images.filter((item) => String(item || '').trim()),
                    });
                    MessagePlugin.success('现场反馈已提交');
                    await Promise.all([loadTasks(), loadOverview()]);
                    await openTask(taskDetail.value.task);
                } finally {
                    feedbackSubmitting.value = false;
                }
            };
            const saveSchedule = async () => {
                if (!hasPermission(PERMISSION.log)) return;
                if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(scheduleForm.time)) {
                    MessagePlugin.warning('执行时间格式应为 HH:MM');
                    return;
                }
                scheduleSaving.value = true;
                try {
                    const data = unpack(await api.put('/schedule', {
                        enabled: !!scheduleForm.enabled,
                        time: scheduleForm.time,
                    }));
                    scheduleInfo.registered = !!data.registered;
                    scheduleInfo.next_run_time = data.next_run_time || '';
                    MessagePlugin.success('日志生成计划已保存');
                } finally {
                    scheduleSaving.value = false;
                }
            };
            const syncNow = async () => {
                if (!hasPermission(PERMISSION.log)) return;
                syncingNow.value = true;
                try {
                    // 手动同步只负责入队：按日事实写入由任务队列执行，页面提示任务号便于追溯。
                    const data = unpack(await api.post('/schedule/run'));
                    lastSyncTaskId.value = data.task_id || 0;
                    MessagePlugin.success('同步任务已入队，执行结果可在任务队列查看');
                    await Promise.all([loadOverview(), loadLogs(), loadSchedule()]);
                } finally {
                    syncingNow.value = false;
                }
            };

            onMounted(async () => {
                try {
                    await reloadAll();
                } finally {
                    initialLoading.value = false;
                }
            });
            return {
                activeTab, initialLoading, errorMessage, overview, logs, filteredTasks, taskFilter, taskColumns,
                scheduleForm, scheduleInfo, scheduleSaving, syncingNow, lastSyncTaskId,
                coordinateText, weatherSourceText, logWeatherText, weatherValue, gddValue, formatDateTime,
                priorityText, priorityTheme, statusText, statusTheme, resultText, resultTheme,
                onTabChange, openLog, logDialogVisible, logDetailLoading, logDetail, taskGenerating,
                generateTaskFromLog, openTask, taskDialogVisible, detailLoading, taskDetail,
                feedbackForm, feedbackSubmitting, addFeedbackImage, removeFeedbackImage, submitFeedback,
                saveSchedule, syncNow,
            };
        },
    });
})();
