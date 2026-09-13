(function () {
    const { createApp, ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    const app = createApp({
        delimiters: ['[[', ']]'],
        setup() {
            const authenticated = ref(false);
            const csrfToken = ref('');
            const state = reactive({ backend: {}, pending_updates: [], pending_error: '', current_operation: null });
            const operation = ref(null);
            const logs = ref([]);
            const logCursor = ref(0);
            const logPanel = ref(null);
            const darkTheme = ref(document.documentElement.getAttribute('data-theme') === 'dark');
            const selectedOperationId = new URLSearchParams(window.location.search).get('operation_id') || '';
            let stateTimer = 0;
            let logTimer = 0;

            const updateColumns = [
                { colKey: 'plugin_id', title: '插件 / 操作编号', minWidth: 310, cell: 'plugin_id' },
                { colKey: 'version', title: '版本', width: 220, cell: 'version' },
                { colKey: 'created_at', title: '确认时间', width: 210 },
                { colKey: 'status', title: '状态', width: 130, cell: 'status' },
                { colKey: 'actions', title: '操作', width: 110, cell: 'actions' }
            ];
            const backend = computed(() => state.backend || {});
            const updates = computed(() => state.pending_updates || []);
            const busy = computed(() => Boolean(state.current_operation && ['queued', 'running'].includes(state.current_operation.status)));
            const backendAvailable = computed(() => ['running', 'degraded'].includes(backend.value.state));
            const backendLabel = computed(() => ({
                stopped: '后端已停止', starting: '后端启动中', running: '后端运行正常', degraded: '后端降级运行',
                stopping: '后端停止中', external: '外部后端进程', failed: '后端运行失败'
            }[backend.value.state] || '正在读取状态'));
            const backendDetail = computed(() => {
                if (backend.value.ownership === 'external') return '8000 端口不受控制中心管理';
                if (backend.value.pid) return '受控进程 PID ' + backend.value.pid;
                return backend.value.last_error || '当前没有受控后端进程';
            });
            const operationStatusLabel = computed(() => ({
                queued: '等待执行', running: '执行中', succeeded: '已完成',
                partial_success: '部分成功', failed: '失败'
            }[operation.value?.status] || '无操作'));
            const operationTheme = computed(() => ({
                queued: 'default', running: 'primary', succeeded: 'success',
                partial_success: 'warning', failed: 'danger'
            }[operation.value?.status] || 'default'));
            const operationMessage = computed(() => operation.value?.message || '尚未执行后端操作或插件更新');
            const phaseIndex = {
                stopping: 0, dry_run: 1, applying: 2, restarting: 3,
                starting: 3, verifying: 4, succeeded: 5, partial_success: 5
            };
            const stepCurrent = computed(() => phaseIndex[operation.value?.phase] ?? 0);
            const stepStatus = computed(() => operation.value?.status === 'failed' ? 'error' : 'process');

            const api = async (path, options = {}) => {
                const headers = { ...(options.headers || {}) };
                if (options.method && options.method !== 'GET') headers['X-Control-CSRF'] = csrfToken.value;
                if (options.body) headers['Content-Type'] = 'application/json';
                const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const detail = data.detail;
                    const message = detail && typeof detail === 'object'
                        ? (detail.message || detail.code || '控制中心请求失败')
                        : (detail || '控制中心请求失败');
                    throw new Error(message);
                }
                return data;
            };

            const establishSession = async () => {
                const fragment = new URLSearchParams(window.location.hash.slice(1));
                const token = fragment.get('token');
                try {
                    if (token) {
                        const response = await fetch('/api/session/bootstrap', {
                            method: 'POST', credentials: 'same-origin',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ token })
                        });
                        if (!response.ok) throw new Error('启动令牌无效');
                        const data = await response.json();
                        csrfToken.value = data.csrf_token;
                        history.replaceState(null, '', window.location.pathname + window.location.search);
                    } else {
                        const data = await api('/api/session');
                        csrfToken.value = data.csrf_token;
                    }
                    authenticated.value = true;
                } catch (error) {
                    authenticated.value = false;
                }
            };

            const fetchState = async () => {
                if (!authenticated.value) return;
                try {
                    const data = await api('/api/state');
                    Object.assign(state, data);
                    if (data.current_operation) {
                        operation.value = data.current_operation;
                    } else if (operation.value && ['queued', 'running'].includes(operation.value.status)) {
                        operation.value = await api('/api/operations/' + encodeURIComponent(operation.value.job_id));
                    }
                    if (!operation.value && selectedOperationId) {
                        const selected = data.pending_updates.find((item) => item.operation_id === selectedOperationId);
                        if (selected) nextTick(() => document.querySelector('.updates-section')?.scrollIntoView({ behavior: 'smooth' }));
                    }
                } catch (error) {
                    MessagePlugin.error(error.message);
                }
            };

            const fetchLogs = async () => {
                if (!authenticated.value) return;
                try {
                    const data = await api('/api/logs?cursor=' + logCursor.value + '&limit=300');
                    if (data.items.length) {
                        logs.value.push(...data.items);
                        if (logs.value.length > 1000) logs.value.splice(0, logs.value.length - 1000);
                        await nextTick();
                        if (logPanel.value) logPanel.value.scrollTop = logPanel.value.scrollHeight;
                    }
                    logCursor.value = data.next_cursor;
                } catch (error) {}
            };

            const submit = async (path) => {
                try {
                    const job = await api(path, { method: 'POST' });
                    operation.value = job;
                    await fetchState();
                } catch (error) {
                    MessagePlugin.error(error.message);
                }
            };

            const runBackendAction = (action) => submit('/api/backend/' + action);
            const confirmApply = (row) => {
                const dialog = DialogPlugin.confirm({
                    header: '应用插件更新',
                    body: '将停止后端并更新 ' + row.plugin_id + '：' + row.current_version + ' → ' + row.target_version + '。开始后不可取消。',
                    confirmBtn: '停止并更新',
                    onConfirm: () => {
                        dialog.destroy();
                        submit('/api/plugin-updates/' + encodeURIComponent(row.operation_id) + '/apply');
                    }
                });
            };
            const confirmApplyAll = () => {
                const dialog = DialogPlugin.confirm({
                    header: '应用全部插件更新',
                    body: '将统一预检并顺序应用 ' + updates.value.length + ' 个更新计划，期间后端暂时离线。开始后不可取消。',
                    confirmBtn: '停止并全部更新',
                    onConfirm: () => {
                        dialog.destroy();
                        submit('/api/plugin-updates/apply-all');
                    }
                });
            };
            const confirmExit = () => {
                const dialog = DialogPlugin.confirm({
                    header: '退出控制中心',
                    body: '退出控制中心会同时停止由它托管的后端服务。',
                    theme: 'danger', confirmBtn: '停止并退出',
                    onConfirm: () => {
                        dialog.destroy();
                        submit('/api/control-center/exit');
                    }
                });
            };
            const toggleTheme = () => {
                darkTheme.value = !darkTheme.value;
                document.documentElement.setAttribute('data-theme', darkTheme.value ? 'dark' : 'light');
                localStorage.setItem('control_center_theme', darkTheme.value ? 'dark' : 'light');
            };
            const openAdmin = () => window.open('http://127.0.0.1:8000/admin/plugin', '_blank', 'noopener');
            const clearVisibleLogs = () => { logs.value = []; };
            const formatLogTime = (value) => value ? new Date(value).toLocaleTimeString('zh-CN', { hour12: false }) : '';

            onMounted(async () => {
                await establishSession();
                await Promise.all([fetchState(), fetchLogs()]);
                stateTimer = window.setInterval(fetchState, 2000);
                logTimer = window.setInterval(fetchLogs, 1000);
            });
            onBeforeUnmount(() => {
                window.clearInterval(stateTimer);
                window.clearInterval(logTimer);
            });

            return {
                authenticated, state, backend, updates, operation, logs, logPanel, darkTheme,
                busy, backendAvailable, backendLabel, backendDetail, updateColumns,
                operationStatusLabel, operationTheme, operationMessage, stepCurrent, stepStatus,
                runBackendAction, confirmApply, confirmApplyAll, confirmExit, toggleTheme,
                openAdmin, clearVisibleLogs, formatLogTime
            };
        }
    });
    app.use(TDesign);
    if (window.TDesignIcons) app.use(window.TDesignIcons);
    app.mount('#control-app');
})();
