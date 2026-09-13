/** 硬件页智能识别快捷检测私有状态模块。 */
(function (window) {
    'use strict';

    const { computed, ref } = Vue;
    const { MessagePlugin } = TDesign;
    const API_BASE = '/plugins/yolo_model_manager/recognitions';
    const FINAL_STATUSES = ['succeeded', 'failed', 'paused', 'cancelled', 'timeout'];

    const hasPermission = (codes, code) => codes.indexOf(code) !== -1
        || codes.indexOf('*') !== -1
        || codes.indexOf(code.split(':')[0] + ':*') !== -1;
    const clamp = (value, min, max) => Math.min(Math.max(Number(value) || 0, min), max);

    function create(options) {
        const pluginChecked = ref(false), pluginEnabled = ref(false), featureAvailable = ref(false);
        const contextLoading = ref(false), detectionContext = ref(null);
        const dialogVisible = ref(false), submitting = ref(false), taskState = ref(null);
        const authCodes = HuiYan.getAuthCodes();
        const permitted = hasPermission(authCodes, 'hardware:data')
            && hasPermission(authCodes, 'yolo_model_manager:detect');
        let pollingGeneration = 0, refreshGeneration = 0, pollingTimer = null, pollingResolve = null;

        const visible = computed(() => permitted && pluginEnabled.value
            && featureAvailable.value && options.currentDevice.value
            && options.currentDevice.value.provider_id === 'hardware_jjr'
            && options.currentDevice.value.device_type === 'growth'
            && (options.currentDevice.value.capabilities || []).includes('realtime'));
        const ready = computed(() => !!(detectionContext.value && detectionContext.value.ready));
        const reason = computed(() => {
            if (contextLoading.value) return '正在检查快捷检测条件';
            return ready.value ? '使用当前显示的最新图片进行检测'
                : ((detectionContext.value && detectionContext.value.reason) || '当前无法快捷检测');
        });
        const working = computed(() => submitting.value
            || ['queued', 'running'].indexOf((taskState.value || {}).status) !== -1);
        const record = computed(() => (taskState.value || {}).record || null);
        const statusTheme = computed(() => ({
            succeeded: 'success', failed: 'error', paused: 'warning',
            cancelled: 'warning', timeout: 'warning',
        }[(taskState.value || {}).status] || 'info'));
        const statusMessage = computed(() => (taskState.value || {}).message || '正在创建检测任务');
        const detectionRows = computed(() => ((record.value || {}).detections || []).map(
            (item, index) => Object.assign({ _rowKey: index }, item)
        ));
        const boxOverlays = computed(() => {
            const data = record.value;
            if (!data || !data.image_width || !data.image_height) return [];
            return (data.detections || []).filter((item) => Array.isArray(item.bbox)
                && item.bbox.length === 4).map((item, index) => {
                const x1 = clamp(item.bbox[0], 0, data.image_width);
                const y1 = clamp(item.bbox[1], 0, data.image_height);
                const x2 = clamp(item.bbox[2], x1, data.image_width);
                const y2 = clamp(item.bbox[3], y1, data.image_height);
                return {
                    key: index + '-' + item.label,
                    label: item.label,
                    confidence: Number(item.confidence || 0),
                    style: {
                        left: (x1 / data.image_width * 100) + '%',
                        top: (y1 / data.image_height * 100) + '%',
                        width: ((x2 - x1) / data.image_width * 100) + '%',
                        height: ((y2 - y1) / data.image_height * 100) + '%',
                    },
                };
            });
        });

        const checkPlugin = () => {
            if (pluginChecked.value) return Promise.resolve(pluginEnabled.value);
            return options.request.get('/plugins/enabled', {
                baseURL: '/api/v1', skipAutoError: true,
            }).then((res) => {
                const names = ((res.data.data || {}).plugins || []);
                pluginEnabled.value = names.indexOf('yolo_model_manager') !== -1;
                return pluginEnabled.value;
            }).catch(() => {
                pluginEnabled.value = false;
                return false;
            }).finally(() => { pluginChecked.value = true; });
        };

        const refreshForDevice = (device) => {
            const generation = ++refreshGeneration;
            detectionContext.value = null;
            featureAvailable.value = false;
            if (!device || device.provider_id !== 'hardware_jjr' || device.device_type !== 'growth'
                || !(device.capabilities || []).includes('realtime') || !permitted) return Promise.resolve();
            contextLoading.value = true;
            return checkPlugin().then((enabled) => {
                if (!enabled) return null;
                return options.request.get(API_BASE + '/detection-context', {
                    params: { device_id: device.id }, skipAutoError: true,
                });
            }).then((res) => {
                if (generation !== refreshGeneration || !res || !options.currentDevice.value
                    || options.currentDevice.value.id !== device.id) return;
                detectionContext.value = res.data.data || {};
                featureAvailable.value = true;
            }).catch(() => {
                if (generation === refreshGeneration) featureAvailable.value = false;
            }).finally(() => {
                if (generation === refreshGeneration) contextLoading.value = false;
            });
        };

        const stopPolling = () => {
            pollingGeneration += 1; window.clearTimeout(pollingTimer);
            if (pollingResolve) pollingResolve(); pollingResolve = null;
        };
        const closeDialog = () => { stopPolling(); };
        const pollTask = async (taskId) => {
            const generation = ++pollingGeneration;
            for (let attempt = 0; attempt < 90; attempt += 1) {
                if (generation !== pollingGeneration || !dialogVisible.value) return;
                try {
                    const res = await options.request.get(
                        API_BASE + '/detection-tasks/' + taskId,
                        { skipAutoError: true }
                    );
                    taskState.value = res.data.data || {};
                    if (FINAL_STATUSES.indexOf(taskState.value.status) !== -1) return;
                } catch (error) {
                    taskState.value = { status: 'failed', message: '检测状态查询失败', record: null };
                    return;
                }
                await new Promise((resolve) => { pollingResolve = resolve; pollingTimer = window.setTimeout(resolve, 2000); });
            }
            taskState.value = {
                status: 'timeout',
                message: '检测仍在后台处理，完成后可在智能识别记录中查看',
                record: null,
            };
        };

        const start = () => {
            const device = options.currentDevice.value;
            if (!device || !ready.value || submitting.value) return;
            stopPolling();
            dialogVisible.value = true;
            submitting.value = true;
            taskState.value = { status: 'queued', message: '正在创建检测任务', record: null };
            options.request.post(API_BASE + '/detection-tasks', { device_id: device.id })
                .then((res) => {
                    const data = res.data.data || {};
                    taskState.value = {
                        task_id: data.task_id,
                        status: data.status || 'queued',
                        message: '检测任务正在排队',
                        record: null,
                    };
                    return pollTask(data.task_id);
                }).catch((error) => {
                    const payload = error.response && error.response.data;
                    taskState.value = {
                        status: 'failed',
                        message: (payload && (payload.detail || payload.msg)) || '检测任务提交失败',
                        record: null,
                    };
                }).finally(() => { submitting.value = false; });
        };

        const formatConfidence = (value) => value !== null && value !== undefined && value !== ''
            && Number.isFinite(Number(value)) ? (Number(value) * 100).toFixed(1) + '%' : '暂无';
        const formatBbox = (bbox) => Array.isArray(bbox)
            ? bbox.map((value) => Number(value).toFixed(1)).join(', ') : '未提供';
        const initialize = () => checkPlugin();
        const dispose = () => { refreshGeneration += 1; stopPolling(); };

        const bindings = {
            quickDetectVisible: visible, quickDetectReady: ready, quickDetectReason: reason,
            quickDetectContextLoading: contextLoading, quickDetectContext: detectionContext,
            quickDetectStart: start, quickDetectDialogVisible: dialogVisible,
            quickDetectSubmitting: submitting, quickDetectState: taskState,
            quickDetectWorking: working, quickDetectRecord: record,
            quickDetectStatusTheme: statusTheme, quickDetectStatusMessage: statusMessage,
            quickDetectRows: detectionRows,
            quickDetectBoxes: boxOverlays,
            closeQuickDetect: closeDialog, formatDetectConfidence: formatConfidence,
            formatDetectBbox: formatBbox,
        };
        return { bindings, refreshForDevice, initialize, dispose };
    }

    window.HuiYanHardwareQuickDetect = { create: create };
})(window);
