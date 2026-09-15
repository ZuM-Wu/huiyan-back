/**
 * 产区管理演示插件 — 管理端页面逻辑
 *
 * 固定演示流程：生成产区日志 -> 模拟 MCP 调用 -> 生成待办任务 -> 提交一次性反馈。
 * 页面展示的产区、天气与积温来自本机真实存储，弹窗仅模拟 MCP 调用轨迹，不发起真实网络请求。
 */
(function () {
    const { ref, computed, reactive, onMounted, nextTick } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    const TAB_KEY = 'logs';
    const TABS = ['logs', 'tasks'];
    const PERMISSION = {
        list: 'production_area_management:list',
        log: 'production_area_management:log:generate',
        task: 'production_area_management:task:generate',
        feedback: 'production_area_management:feedback',
        reset: 'production_area_management:reset',
    };

    const emptyOverview = {
        readiness: { ready: false, message: '' },
        area: null,
        weather: null,
        gdd: null,
        latest_log_exists: false,
        latest_log_id: 0,
        generated_task_exists: false,
        generated_task_id: 0,
        log_count: 0,
        task_stats: { total: 0, pending: 0, completed: 0 },
    };

    HuiYan.createPluginPage({
        plugin: 'production_area_management',
        page: 'index',
        setup(context) {
            const api = context.api;
            const authCodes = HuiYan.getAuthCodes();

            const activeTab = ref(HuiYan.getUrlTab(TAB_KEY, TABS));
            const initialLoading = ref(true);
            const errorMessage = ref('');
            const overview = ref(Object.assign({}, emptyOverview));
            const logs = ref([]);
            const tasks = ref([]);
            const taskFilter = ref('all');
            const highlightTaskId = ref(0);

            const generatingLog = ref(false);
            const drawerVisible = ref(false);
            const detailLoading = ref(false);
            const taskDetail = ref(null);
            const feedbackSubmitting = ref(false);
            const feedbackForm = reactive({ result: 'success', content: '', metrics: [] });

            const mcpVisible = ref(false);
            const mcpRunning = ref(false);
            const mcpFinished = ref(false);
            const mcpSteps = ref([]);
            const generatedTaskId = ref(0);

            const moreOptions = [{ content: '恢复演示初始状态', value: 'reset' }];
            const taskColumns = [
                { colKey: 'title', title: '任务', width: 220, cell: 'title', ellipsis: true },
                { colKey: 'priority', title: '优先级', width: 76, cell: 'priority' },
                { colKey: 'assignee', title: '负责人', width: 88 },
                { colKey: 'plan_time', title: '计划时间', width: 140, cell: 'plan_time' },
                { colKey: 'status', title: '状态', width: 80, cell: 'status' },
                { colKey: 'feedback', title: '反馈摘要', width: 210, cell: 'feedback', ellipsis: true },
                { colKey: 'operation', title: '操作', width: 76, cell: 'operation' },
            ];

            const canGenerateLog = computed(function () {
                return authCodes.indexOf(PERMISSION.log) !== -1 && !overview.value.latest_log_exists;
            });
            const canReset = computed(function () {
                return authCodes.indexOf(PERMISSION.reset) !== -1;
            });

            const areaRegion = computed(function () {
                const area = overview.value.area;
                if (!area) { return '请先在本机维护产区数据'; }
                return [area.province, area.city, area.district].filter(Boolean).join(' / ') || '未维护行政区域';
            });

            const coordinateText = computed(function () {
                const area = overview.value.area;
                if (!area) { return ''; }
                const lng = Number(area.longitude || 0);
                const lat = Number(area.latitude || 0);
                if (!lng && !lat) { return '未维护经纬度'; }
                return '东经 ' + lng.toFixed(3) + ' / 北纬 ' + lat.toFixed(3);
            });

            const weatherSourceText = computed(function () {
                const weather = overview.value.weather;
                if (!weather) { return '等待天气服务同步'; }
                const parts = [];
                if (weather.source) { parts.push('数据源 ' + weather.source); }
                if (weather.obs_time) { parts.push('观测 ' + weather.obs_time); }
                return parts.join(' · ') || '本机天气快照';
            });

            const filteredTasks = computed(function () {
                if (taskFilter.value === 'all') { return tasks.value; }
                return tasks.value.filter(function (item) { return item.status === taskFilter.value; });
            });

            const weatherValue = function (key, fallback) {
                const weather = overview.value.weather;
                if (!weather || weather[key] === undefined || weather[key] === null || weather[key] === '') {
                    return fallback;
                }
                return weather[key];
            };

            const gddValue = function (key, fallback) {
                const gdd = overview.value.gdd;
                if (!gdd || gdd[key] === undefined || gdd[key] === null || gdd[key] === '') {
                    return fallback;
                }
                return gdd[key];
            };

            const formatDateTime = function (value) {
                if (!value) { return ''; }
                return String(value).replace('T', ' ').slice(0, 16);
            };

            const priorityText = function (value) {
                return { high: '高', medium: '中', low: '低' }[value] || value || '中';
            };

            const priorityTheme = function (value) {
                return { high: 'danger', medium: 'warning', low: 'default' }[value] || 'default';
            };

            const statusText = function (value) {
                return { pending: '待处理', completed: '已完成' }[value] || value || '待处理';
            };

            const statusTheme = function (value) {
                return value === 'completed' ? 'success' : 'primary';
            };

            const resultText = function (value) {
                return { success: '完成', partial: '部分完成', failed: '未完成' }[value] || value;
            };

            const resultTheme = function (value) {
                return { success: 'success', partial: 'warning', failed: 'danger' }[value] || 'default';
            };

            const metricEntries = function (metrics) {
                return Object.keys(metrics || {}).map(function (key) {
                    return { key: key, value: metrics[key] };
                });
            };

            const onTabChange = function (value) {
                HuiYan.syncUrlTab(value);
            };

            const loadOverview = async function () {
                try {
                    overview.value = await api.get('/overview').then(function (res) { return res.data.data; });
                } catch (error) {
                    errorMessage.value = '产区与天气概览加载失败，请检查本机产区与天气服务状态。';
                }
            };

            const loadLogs = async function () {
                const data = await api.get('/logs').then(function (res) { return res.data.data; });
                logs.value = (data && data.list) || [];
            };

            const loadTasks = async function () {
                const data = await api.get('/tasks', { params: { status: 'all' } })
                    .then(function (res) { return res.data.data; });
                tasks.value = (data && data.list) || [];
            };

            const reloadAll = async function () {
                await Promise.all([loadOverview(), loadLogs(), loadTasks()]);
            };

            const generateLog = async function () {
                if (!canGenerateLog.value) { return; }
                generatingLog.value = true;
                try {
                    await api.post('/logs/generate');
                    MessagePlugin.success('产区日志已生成');
                    await reloadAll();
                } catch (error) {
                    errorMessage.value = '日志生成失败，请检查本机产区与天气服务状态。';
                } finally {
                    generatingLog.value = false;
                }
            };

            const addMetric = function () {
                feedbackForm.metrics.push({ name: '', value: '' });
            };

            const removeMetric = function (index) {
                feedbackForm.metrics.splice(index, 1);
            };

            const resetFeedbackForm = function () {
                feedbackForm.result = 'success';
                feedbackForm.content = '';
                feedbackForm.metrics = [];
            };

            const openTask = async function (row) {
                drawerVisible.value = true;
                detailLoading.value = true;
                taskDetail.value = null;
                resetFeedbackForm();
                try {
                    taskDetail.value = await api.get('/tasks/' + row.id)
                        .then(function (res) { return res.data.data; });
                } catch (error) {
                    drawerVisible.value = false;
                } finally {
                    detailLoading.value = false;
                }
            };

            const submitFeedback = async function () {
                if (!taskDetail.value) { return; }
                const content = (feedbackForm.content || '').trim();
                if (content.length < 5) {
                    MessagePlugin.warning('请填写至少 5 个字的反馈正文');
                    return;
                }
                const metrics = {};
                feedbackForm.metrics.forEach(function (item) {
                    const name = (item.name || '').trim();
                    if (name) { metrics[name] = item.value; }
                });
                feedbackSubmitting.value = true;
                try {
                    const taskId = taskDetail.value.task.id;
                    const result = await api.post('/tasks/' + taskId + '/feedback', {
                        result: feedbackForm.result, content: content, metrics: metrics,
                    }).then(function (res) { return res.data.data; });
                    MessagePlugin.success('反馈已提交，任务已完成');
                    taskDetail.value.task = result.task;
                    await loadTasks();
                    await openTask({ id: taskId });
                } catch (error) {
                    // 统一响应拦截器已提示后端业务错误，这里只恢复按钮状态
                } finally {
                    feedbackSubmitting.value = false;
                }
            };

            const buildPendingSteps = function () {
                return [
                    { seq: 1, step: '读取最新产区日志', tool: 'plugin_log_latest', status: 'pending', duration_ms: 0, summary: '' },
                    { seq: 2, step: '读取产区树', tool: 'core_agri_area_tree', status: 'pending', duration_ms: 0, summary: '' },
                    { seq: 3, step: '读取天气快照', tool: 'core_agri_weather_snapshot', status: 'pending', duration_ms: 0, summary: '' },
                    { seq: 4, step: '读取逐日天气并计算积温', tool: 'core_agri_weather_daily', status: 'pending', duration_ms: 0, summary: '' },
                    { seq: 5, step: '模型推理生成任务', tool: 'model_reasoning', status: 'pending', duration_ms: 0, summary: '' },
                ];
            };

            const wait = function (ms) {
                return new Promise(function (resolve) { setTimeout(resolve, ms); });
            };

            const openMcpDialog = function () {
                generatedTaskId.value = 0;
                mcpFinished.value = false;
                mcpRunning.value = false;
                mcpSteps.value = buildPendingSteps();
                mcpVisible.value = true;
            };

            const runMcpFlow = async function () {
                if (mcpRunning.value || mcpFinished.value) { return; }
                mcpRunning.value = true;
                for (let index = 0; index < mcpSteps.value.length; index += 1) {
                    mcpSteps.value[index].status = 'running';
                    await nextTick();
                    await wait(420 + index * 60);
                    mcpSteps.value[index].status = 'success';
                    mcpSteps.value[index].duration_ms = 120 + index * 70;
                }
                try {
                    const result = await api.post('/tasks/generate')
                        .then(function (res) { return res.data.data; });
                    generatedTaskId.value = result.task.id;
                    mcpSteps.value.forEach(function (step, index) {
                        step.status = 'success';
                        if (!step.duration_ms) { step.duration_ms = 120 + index * 70; }
                    });
                    mcpFinished.value = true;
                    await loadTasks();
                    await loadOverview();
                } catch (error) {
                    const last = mcpSteps.value[mcpSteps.value.length - 1];
                    last.status = 'failed';
                    last.error = '任务生成失败，请先生成最新的产区日志后重试。';
                } finally {
                    mcpRunning.value = false;
                }
            };

            const closeMcp = function () {
                if (mcpRunning.value) { return; }
                mcpVisible.value = false;
                if (mcpFinished.value) {
                    taskFilter.value = 'all';
                }
            };

            const viewGeneratedTask = async function () {
                mcpVisible.value = false;
                activeTab.value = 'tasks';
                onTabChange('tasks');
                taskFilter.value = 'all';
                await loadTasks();
                highlightTaskId.value = generatedTaskId.value;
                await nextTick();
                const row = document.querySelector('.pam-task-table .pam-row-highlight');
                if (row && typeof row.scrollIntoView === 'function') {
                    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }
            };

            const runReset = async function () {
                try {
                    await api.post('/demo/reset');
                    MessagePlugin.success('演示初始状态已恢复');
                    activeTab.value = 'logs';
                    onTabChange('logs');
                    taskFilter.value = 'all';
                    highlightTaskId.value = 0;
                    await reloadAll();
                } catch (error) {
                    // 统一响应拦截器已提示失败原因
                }
            };

            const confirmReset = function () {
                if (!canReset.value || !DialogPlugin) { return; }
                DialogPlugin.confirm({
                    header: '恢复演示初始状态',
                    body: '将清空当前日志、任务与反馈，并重新播种演示数据。该操作不可撤销。',
                    confirmBtn: { content: '确认恢复', theme: 'danger' },
                    cancelBtn: '取消',
                    onConfirm: function (instance) {
                        if (instance && instance.destroy) { instance.destroy(); }
                        runReset();
                    },
                    onCancel: function (instance) {
                        if (instance && instance.destroy) { instance.destroy(); }
                    },
                });
            };

            const rowClassName = function (params) {
                const row = params && params.row ? params.row : params;
                if (row && row.id && row.id === highlightTaskId.value) { return 'pam-row-highlight'; }
                return '';
            };

            const onMoreAction = function (item) {
                const value = item && item.value !== undefined
                    ? item.value
                    : (item && item.data ? item.data.value : '');
                if (value === 'reset') { confirmReset(); }
            };

            onMounted(async function () {
                try {
                    await reloadAll();
                } finally {
                    initialLoading.value = false;
                }
            });

            return {
                activeTab, initialLoading, errorMessage, overview, logs, filteredTasks, tasks,
                taskFilter, taskColumns, moreOptions, canGenerateLog, canReset,
                areaRegion, coordinateText, weatherSourceText,
                weatherValue, gddValue, formatDateTime, priorityText, priorityTheme,
                statusText, statusTheme, resultText, resultTheme, metricEntries,
                generatingLog, generateLog, onTabChange, onMoreAction,
                drawerVisible, detailLoading, taskDetail, openTask,
                feedbackSubmitting, feedbackForm, addMetric, removeMetric, submitFeedback,
                mcpVisible, mcpRunning, mcpFinished, mcpSteps, openMcpDialog, runMcpFlow, rowClassName,
                closeMcp, viewGeneratedTask, highlightTaskId,
            };
        },
    });
})();
