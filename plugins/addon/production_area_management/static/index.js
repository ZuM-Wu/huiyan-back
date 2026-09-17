/**
 * 产区管理插件管理端页面逻辑。
 * 页面只展示真实按日事实、整改建议和现场反馈，不再模拟 AI、MCP 或固定演示动作。
 */
(function () {
    const { ref, computed, reactive, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    const TAB_KEY = 'tab';
    const TABS = ['logs', 'tasks', 'schedule'];
    // 日报图片上限与模板中"最多 9 张"的文案保持一致
    const LOG_IMAGE_MAX = 9;
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
            const request = context.request;
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
            const logImagesDraft = ref([]);
            const logImagesSaving = ref(false);
            const logImagesUploading = ref(false);
            const logImagesDragOver = ref(false);
            const logImagePolicy = ref({ extensions: [], max_size_mb: 0 });
            const taskGenerating = ref(false);
            const taskDialogVisible = ref(false);
            const detailLoading = ref(false);
            const taskDetail = ref(null);
            const feedbackSubmitting = ref(false);
            const feedbackFormRef = ref(null);
            const feedbackForm = reactive({ result: 'success', content: '', images: [] });
            // 现场说明按规范用 TDesign Form 内联校验，不再用 MessagePlugin 提示格式问题
            const feedbackRules = {
                content: [
                    {
                        validator: (value) => String(value || '').trim().length >= 5,
                        message: '请填写至少 5 个字的现场说明',
                        type: 'error',
                    },
                ],
            };
            const scheduleSaving = ref(false);
            const syncingNow = ref(false);
            const scheduleForm = reactive({ enabled: true, time: '06:00' });
            const scheduleInfo = reactive({ registered: false, next_run_time: '' });
            const lastSyncTaskId = ref(0);

            // 列宽交由表格内容驱动（视觉规范：内容驱动自动列宽），仅标题列显式省略
            const taskColumns = [
                { colKey: 'title', title: '任务', cell: 'title', ellipsis: true },
                { colKey: 'priority', title: '优先级', cell: 'priority' },
                { colKey: 'assignee', title: '负责人' },
                { colKey: 'plan_time', title: '计划时间', cell: 'plan_time' },
                { colKey: 'status', title: '状态', cell: 'status' },
                { colKey: 'feedback', title: '现场反馈', cell: 'feedback' },
                { colKey: 'operation', title: '操作', cell: 'operation' },
            ];
            // 执行时间用 TDesign Form 内联校验，替代 MessagePlugin 格式提示
            const scheduleRules = {
                time: [
                    { required: true, message: '请输入执行时间', type: 'error' },
                    {
                        validator: (value) => /^([01]\d|2[0-3]):[0-5]\d$/.test(String(value || '')),
                        message: '执行时间格式应为 HH:MM',
                        type: 'error',
                    },
                ],
            };
            const hasPermission = (code) => authCodes.indexOf(code) !== -1;
            const filteredTasks = computed(() => taskFilter.value === 'all'
                ? tasks.value : tasks.value.filter((item) => item.status === taskFilter.value));
            const canUpdateLogImages = computed(() => hasPermission(PERMISSION.log));
            // 任务未完成且详情已加载时展示反馈表单与弹窗 footer 操作
            const canSubmitFeedback = computed(() => !!taskDetail.value && taskDetail.value.task.status !== 'completed');
            // 整个日报图片区块作为拖放区域的前提：有权限、不在保存/上传中且未达上限
            const canDropLogImages = computed(() => canUpdateLogImages.value
                && !logImagesSaving.value && !logImagesUploading.value
                && logImagesDraft.value.length < LOG_IMAGE_MAX);
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
                logImagesDraft.value = [];
                try {
                    const detail = unpack(await api.get(`/logs/${log.id}`));
                    logDetail.value = detail;
                    logImagesDraft.value = [...(detail.log.images || [])];
                } finally {
                    logDetailLoading.value = false;
                }
            };
            const saveLogImages = async (images, successMessage) => {
                if (!logDetail.value || !canUpdateLogImages.value || logImagesSaving.value) return false;
                const targetLogId = logDetail.value.log.id;
                const savedImages = [...(logDetail.value.log.images || [])];
                logImagesSaving.value = true;
                try {
                    const data = unpack(await api.put(`/logs/${targetLogId}/images`, {
                        images,
                    }));
                    // 保存响应可能晚于用户切换日报，只回填请求对应的当前详情。
                    if (logDetail.value && logDetail.value.log.id === targetLogId) {
                        logDetail.value.log = data.log;
                        logImagesDraft.value = [...(data.log.images || [])];
                    }
                    const index = logs.value.findIndex((item) => item.id === data.log.id);
                    if (index !== -1) logs.value[index] = Object.assign({}, logs.value[index], data.log);
                    MessagePlugin.success(successMessage);
                    return true;
                } catch (error) {
                    if (logDetail.value && logDetail.value.log.id === targetLogId) {
                        logImagesDraft.value = savedImages;
                    }
                    return false;
                } finally {
                    logImagesSaving.value = false;
                }
            };
            const removeLogImage = (index) => {
                const images = logImagesDraft.value.filter((_, itemIndex) => itemIndex !== index);
                logImagesDraft.value = images;
                saveLogImages(images, '日报图片已删除');
            };
            // 惰性加载并缓存服务端上传策略（扩展名、大小上限），与全局 image-upload 组件同源
            const ensureLogImagePolicy = async () => {
                if (logImagePolicy.value.extensions.length) return logImagePolicy.value;
                try {
                    const limits = await window.UploadPolicyClient.getImageLimits();
                    logImagePolicy.value = {
                        extensions: limits.image_extensions,
                        max_size_mb: limits.image_max_size_mb,
                    };
                } catch (error) {
                    MessagePlugin.error('获取上传规则失败，请刷新重试');
                }
                return logImagePolicy.value;
            };
            // 拖入文件统一校验后逐张上传，成功即并入草稿并保存日报图片
            const uploadLogImageFiles = async (fileList) => {
                if (!canDropLogImages.value) return;
                const policy = await ensureLogImagePolicy();
                if (!policy.extensions.length) return;
                let files = Array.prototype.slice.call(fileList || []);
                const room = LOG_IMAGE_MAX - logImagesDraft.value.length;
                if (files.length > room) {
                    MessagePlugin.warning(`本次最多还能上传 ${room} 张图片`);
                    files = files.slice(0, room);
                }
                files = files.filter((file) => {
                    const extension = String(file.name || '').split('.').pop().toLowerCase();
                    if (policy.extensions.indexOf(extension) === -1) {
                        MessagePlugin.error(`仅支持 ${policy.extensions.join('/')} 图片`);
                        return false;
                    }
                    if (file.size > policy.max_size_mb * 1024 * 1024) {
                        MessagePlugin.error(`图片大小不能超过 ${policy.max_size_mb}MB`);
                        return false;
                    }
                    return true;
                });
                if (!files.length) return;
                logImagesUploading.value = true;
                const uploaded = [];
                let failures = 0;
                try {
                    for (const file of files) {
                        const formData = new FormData();
                        formData.append('file', file);
                        try {
                            const response = await request.post('/upload/image', formData);
                            const payload = response && response.data ? (response.data.data || response.data) : response;
                            const url = payload && payload.url;
                            if (url && uploaded.indexOf(url) === -1 && logImagesDraft.value.indexOf(url) === -1) {
                                uploaded.push(url);
                            }
                        } catch (error) {
                            failures += 1;
                        }
                    }
                } finally {
                    logImagesUploading.value = false;
                }
                if (failures) MessagePlugin.error(`${failures} 张图片上传失败，请重试`);
                if (uploaded.length) {
                    const images = logImagesDraft.value.concat(uploaded).slice(0, LOG_IMAGE_MAX);
                    logImagesDraft.value = images;
                    await saveLogImages(images, '日报图片已上传');
                }
            };
            // 拖拽高亮用计数器维护：进入/离开子元素会成对触发 enter/leave
            let logImageDragDepth = 0;
            const onLogImagesDragEnter = () => {
                if (!canDropLogImages.value) return;
                logImageDragDepth += 1;
                logImagesDragOver.value = true;
            };
            // dragover 需持续阻止默认行为，浏览器才允许把文件释放到区块上
            const onLogImagesDragOver = () => {};
            const onLogImagesDragLeave = () => {
                logImageDragDepth = Math.max(0, logImageDragDepth - 1);
                if (!logImageDragDepth) logImagesDragOver.value = false;
            };
            const onLogImagesDrop = (event) => {
                logImageDragDepth = 0;
                logImagesDragOver.value = false;
                // 无权限或正在保存/上传时静默忽略；仅"已达上限"给出明确提示
                if (!canUpdateLogImages.value || logImagesSaving.value || logImagesUploading.value) return;
                if (logImagesDraft.value.length >= LOG_IMAGE_MAX) {
                    MessagePlugin.warning(`最多上传 ${LOG_IMAGE_MAX} 张图片`);
                    return;
                }
                const files = event.dataTransfer && event.dataTransfer.files;
                if (files && files.length) uploadLogImageFiles(files);
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
            const submitFeedback = async () => {
                if (!taskDetail.value || !hasPermission(PERMISSION.feedback)) return;
                // 内联校验未通过时错误已显示在字段下方，直接中断
                const validateResult = await feedbackFormRef.value.validate();
                if (validateResult !== true) return;
                feedbackSubmitting.value = true;
                try {
                    await api.post(`/tasks/${taskDetail.value.task.id}/feedback`, {
                        result: feedbackForm.result,
                        content: String(feedbackForm.content || '').trim(),
                        images: feedbackForm.images.filter((item) => String(item || '').trim()),
                    });
                    MessagePlugin.success('现场反馈已提交');
                    await Promise.all([loadTasks(), loadOverview()]);
                    await openTask(taskDetail.value.task);
                } finally {
                    feedbackSubmitting.value = false;
                }
            };
            const saveSchedule = async (params) => {
                // 表单 submit 回调：内联校验未通过时错误已显示在字段下方，直接中断
                if (params && params.e) params.e.preventDefault();
                if (params && params.validateResult !== true) return;
                if (!hasPermission(PERMISSION.log)) return;
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
            // 演示控制器（index-demo.js）：重置演示卡片 + 生成任务前的模拟思考弹窗
            const demo = window.PAMDemo.createDemoAddons({
                api,
                message: MessagePlugin,
                reload: () => Promise.all([loadLogs(), loadOverview()]),
                generate: generateTaskFromLog,
                logDetail: logDetail,
            });
            return {
                activeTab, initialLoading, errorMessage, overview, logs, filteredTasks, taskFilter, taskColumns,
                scheduleForm, scheduleInfo, scheduleRules, scheduleSaving, syncingNow, lastSyncTaskId,
                coordinateText, weatherSourceText, logWeatherText, weatherValue, gddValue, formatDateTime,
                priorityText, priorityTheme, statusText, statusTheme, resultText, resultTheme,
                onTabChange, openLog, logDialogVisible, logDetailLoading, logDetail, taskGenerating,
                logImagesDraft, logImagesSaving, logImagesDragOver, canUpdateLogImages,
                onLogImagesDragEnter, onLogImagesDragOver, onLogImagesDragLeave, onLogImagesDrop,
                removeLogImage, generateTaskFromLog, openTask, taskDialogVisible, detailLoading, taskDetail,
                feedbackForm, feedbackRules, feedbackFormRef, feedbackSubmitting, canSubmitFeedback, submitFeedback,
                saveSchedule, syncNow,
                ...demo.bindings,
            };
        },
    });
})();
