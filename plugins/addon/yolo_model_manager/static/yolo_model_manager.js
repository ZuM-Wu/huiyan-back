/**
 * 智能识别插件管理端页面。
 * 识别记录与模型 API 使用插件相对客户端，上传策略复用全局管理端请求客户端。
 */
(function () {
    const { ref, reactive, computed, watch, onBeforeUnmount } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPluginPage({
        plugin: 'yolo_model_manager',
        page: 'yolo_model_manager',
        setup({ api, request }) {
            const pageController = new AbortController();
            let pageActive = true;
            const requestConfig = (config) => ({ ...(config || {}), signal: pageController.signal });
            const isPageActive = () => pageActive && !pageController.signal.aborted;
            onBeforeUnmount(() => {
                pageActive = false;
                pageController.abort();
            });

            const unwrap = (response) => {
                const body = response && response.data ? response.data : {};
                return body.data || body;
            };

            const activeTab = ref(HuiYan.getUrlTab('records', ['records', 'models']));

            const records = ref([]);
            const recordLoading = ref(false);
            const recordFilters = reactive({ plot_id: null, model_id: null });
            const recordOptions = reactive({ plots: [], models: [] });
            const recordPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const recordColumns = [
                { colKey: 'image', title: '标注图', width: 88, cell: 'image' },
                { colKey: 'plot', title: '地块', minWidth: 120, cell: 'plot' },
                { colKey: 'record_model', title: '模型', minWidth: 120, cell: 'record_model' },
                { colKey: 'result', title: '识别结果', minWidth: 140, cell: 'result' },
                { colKey: 'detection_count', title: '目标数', width: 82 },
                { colKey: 'confidence', title: '最高置信度', width: 110, cell: 'confidence' },
                { colKey: 'recognized_at', title: '识别时间', width: 170 },
                { colKey: 'record_actions', title: '操作', width: 72, cell: 'record_actions' }
            ];

            const fetchRecordOptions = async () => {
                const response = await api.get('/recognitions/options', requestConfig());
                if (!isPageActive()) return;
                const data = unwrap(response);
                recordOptions.plots = data.plots || [];
                recordOptions.models = data.models || [];
            };

            const fetchRecords = async () => {
                recordLoading.value = true;
                try {
                    const response = await api.get('/recognitions', requestConfig({
                        params: {
                            plot_id: recordFilters.plot_id || undefined,
                            model_id: recordFilters.model_id || undefined,
                            page: recordPagination.current,
                            limit: recordPagination.pageSize
                        }
                    }));
                    const data = unwrap(response);
                    if (!isPageActive()) return;
                    records.value = data.list || [];
                    recordPagination.total = data.total || 0;
                } finally {
                    recordLoading.value = false;
                }
            };

            const searchRecords = () => {
                recordPagination.current = 1;
                fetchRecords();
            };

            const resetRecords = () => {
                recordFilters.plot_id = null;
                recordFilters.model_id = null;
                recordPagination.current = 1;
                fetchRecords();
            };

            const onRecordPageChange = (pageInfo) => {
                recordPagination.current = pageInfo.current;
                recordPagination.pageSize = pageInfo.pageSize;
                fetchRecords();
            };

            const formatConfidence = window.HuiYanYoloFormat.formatConfidence;
            const formatBbox = window.HuiYanYoloFormat.formatBbox;

            const recordDetailVisible = ref(false);
            const recordDetailLoading = ref(false);
            const recordDetail = ref(null);
            const detectionColumns = [
                { colKey: 'label', title: '标签', minWidth: 180 },
                {
                    colKey: 'detection_confidence', title: '置信度', width: 110,
                    cell: 'detection_confidence'
                },
                { colKey: 'bbox', title: '目标框 x1, y1, x2, y2', minWidth: 260, cell: 'bbox' }
            ];
            const recordDetailDetections = computed(() =>
                ((recordDetail.value && recordDetail.value.detections) || []).map((item, index) => ({
                    ...item,
                    row_key: index + 1
                })));

            const openRecordDetail = async (row) => {
                recordDetail.value = null;
                recordDetailVisible.value = true;
                recordDetailLoading.value = true;
                try {
                    const response = await api.get('/recognitions/' + row.id, requestConfig());
                    if (!isPageActive()) return;
                    recordDetail.value = unwrap(response);
                } finally {
                    recordDetailLoading.value = false;
                }
            };

            const models = ref([]);
            const loading = ref(false);
            const filters = reactive({ keyword: '', format: '', binding_status: '' });
            const pagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const formatOptions = [
                { label: 'PT', value: 'pt' },
                { label: 'ONNX', value: 'onnx' }
            ];
            const bindingStatusOptions = [
                { label: '已绑定', value: 'bound' },
                { label: '未绑定', value: 'unbound' }
            ];
            const columns = [
                { colKey: 'model', title: '模型', minWidth: 230, cell: 'model' },
                { colKey: 'file', title: '文件', width: 90, cell: 'file' },
                { colKey: 'labels', title: '模型标签', minWidth: 150, cell: 'labels' },
                { colKey: 'bindings', title: '绑定地块', minWidth: 170, cell: 'bindings' },
                {
                    colKey: 'default_confidence', title: '默认置信度', width: 110,
                    cell: 'default_confidence'
                },
                { colKey: 'create_time', title: '上传时间', width: 150 },
                { colKey: 'actions', title: '操作', width: 200, cell: 'actions', fixed: 'right' }
            ];

            const fetchModels = async () => {
                loading.value = true;
                try {
                    const response = await api.get('/models', requestConfig({
                        params: {
                            keyword: filters.keyword.trim(),
                            format: filters.format || '',
                            binding_status: filters.binding_status || '',
                            page: pagination.current,
                            limit: pagination.pageSize
                        }
                    }));
                    const data = unwrap(response);
                    if (!isPageActive()) return;
                    models.value = data.list || [];
                    pagination.total = data.total || 0;
                } finally {
                    loading.value = false;
                }
            };

            const searchModels = () => {
                pagination.current = 1;
                fetchModels();
            };

            const resetModels = () => {
                filters.keyword = '';
                filters.format = '';
                filters.binding_status = '';
                pagination.current = 1;
                fetchModels();
            };

            const onPageChange = (pageInfo) => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                fetchModels();
            };

            const formatSize = window.HuiYanYoloFormat.formatSize;
            const shortHash = window.HuiYanYoloFormat.shortHash;

            const uploadPolicy = ref({ max_size_mb: 500, extensions: ['pt', 'onnx'] });
            const uploadPolicyReady = ref(false);
            const uploadAccept = computed(() =>
                uploadPolicy.value.extensions.map((extension) => '.' + extension).join(','));
            const uploadHint = computed(() =>
                uploadPolicy.value.extensions.map((item) => item.toUpperCase()).join(' / ')
                + '，最大 ' + uploadPolicy.value.max_size_mb + 'MB');

            const fetchUploadPolicy = async () => {
                try {
                    const response = await request.get('/upload/settings', requestConfig());
                    if (!isPageActive()) return;
                    const data = unwrap(response);
                    const policy = (data.policies || []).find(
                        (item) => item.id === 'yolo_model_manager.model');
                    if (!policy) throw new Error('模型上传策略不可用');
                    uploadPolicy.value = policy;
                    uploadPolicyReady.value = true;
                } catch (error) {
                    if (!isPageActive()) return;
                    uploadPolicyReady.value = false;
                    MessagePlugin.error('获取模型上传规则失败，请刷新重试');
                }
            };

            const modelDialogVisible = ref(false);
            const modelDialogTitle = ref('上传模型');
            const modelFormRef = ref(null);
            const editingId = ref(0);
            const submitting = ref(false);
            const uploadFiles = ref([]);
            const modelForm = reactive({
                name: '', version: '', description: '', confidence_percent: 25, upload: ''
            });
            const modelRules = {
                upload: [{
                    validator: () => ({
                        result: editingId.value > 0 || uploadFiles.value.length > 0,
                        message: '请选择模型文件'
                    }),
                    type: 'error'
                }],
                name: [{ required: true, message: '请输入模型名称', type: 'error' }],
                confidence_percent: [{
                    validator: (value) => ({
                        result: Number.isFinite(Number(value))
                            && Number(value) >= 1 && Number(value) <= 100,
                        message: '默认置信度必须在 1% 到 100% 之间'
                    }),
                    type: 'error'
                }]
            };

            const openUpload = () => {
                if (!uploadPolicyReady.value) {
                    MessagePlugin.error('上传规则尚未加载完成');
                    return;
                }
                editingId.value = 0;
                modelDialogTitle.value = '上传模型';
                modelForm.name = '';
                modelForm.version = '';
                modelForm.description = '';
                modelForm.confidence_percent = 25;
                uploadFiles.value = [];
                modelDialogVisible.value = true;
            };

            const openEdit = (row) => {
                editingId.value = row.id;
                modelDialogTitle.value = '编辑模型信息';
                modelForm.name = row.name;
                modelForm.version = row.version || '';
                modelForm.description = row.description || '';
                modelForm.confidence_percent = Math.round(
                    Number(row.default_confidence || 0.25) * 100
                );
                uploadFiles.value = [];
                modelDialogVisible.value = true;
            };

            const selectedUploadFile = () => {
                const item = uploadFiles.value[0];
                return item && (item.raw || item);
            };

            const submitModel = async () => {
                if (!modelFormRef.value) return;
                const valid = await modelFormRef.value.validate();
                if (valid !== true || !modelForm.name.trim()) return;
                submitting.value = true;
                try {
                    if (editingId.value) {
                        await api.patch('/models/' + editingId.value, {
                            name: modelForm.name.trim(),
                            version: modelForm.version.trim(),
                            description: modelForm.description.trim(),
                            default_confidence: Number(modelForm.confidence_percent) / 100
                        });
                        MessagePlugin.success('模型信息已更新');
                    } else {
                        const file = selectedUploadFile();
                        if (!file) return;
                        const extension = String(file.name || '').split('.').pop().toLowerCase();
                        const maxBytes = uploadPolicy.value.max_size_mb * 1024 * 1024;
                        if (!uploadPolicy.value.extensions.includes(extension) || file.size > maxBytes) {
                            MessagePlugin.error('模型格式或大小不符合上传设置');
                            return;
                        }
                        const formData = new FormData();
                        formData.append('file', file);
                        formData.append('name', modelForm.name.trim());
                        formData.append('version', modelForm.version.trim());
                        formData.append('description', modelForm.description.trim());
                        formData.append(
                            'default_confidence',
                            String(Number(modelForm.confidence_percent) / 100)
                        );
                        const response = await api.post('/models', formData, requestConfig());
                        const labels = unwrap(response).labels || [];
                        MessagePlugin.success('模型上传成功，识别 ' + labels.length + ' 个标签');
                    }
                    modelDialogVisible.value = false;
                    await fetchModels();
                } finally {
                    submitting.value = false;
                }
            };

            const bindingDialogVisible = ref(false);
            const bindingLoading = ref(false);
            const bindingSubmitting = ref(false);
            const bindingAreas = ref([]);
            const bindingModel = reactive({ id: 0, name: '' });
            const bindingForm = reactive({ plot_ids: [] });

            const openBindings = async (row) => {
                bindingModel.id = row.id;
                bindingModel.name = row.name;
                bindingForm.plot_ids = [...(row.plot_ids || [])];
                bindingAreas.value = [];
                bindingDialogVisible.value = true;
                bindingLoading.value = true;
                try {
                    const response = await api.get('/plots', requestConfig({ params: { model_id: row.id } }));
                    if (!isPageActive()) return;
                    bindingAreas.value = unwrap(response).areas || [];
                } finally {
                    bindingLoading.value = false;
                }
            };

            const submitBindings = async () => {
                bindingSubmitting.value = true;
                try {
                    await api.put('/models/' + bindingModel.id + '/plots', {
                        plot_ids: bindingForm.plot_ids
                    }, requestConfig());
                    MessagePlugin.success(bindingForm.plot_ids.length ? '地块绑定已更新' : '已解绑全部地块');
                    bindingDialogVisible.value = false;
                    await fetchModels();
                } finally {
                    bindingSubmitting.value = false;
                }
            };

            const downloadModel = async (row) => {
                try {
                    const response = await api.get('/models/' + row.id + '/download', requestConfig({
                        responseType: 'blob',
                        skipAutoError: true
                    }));
                    if (!isPageActive()) return;
                    const disposition = response.headers['content-disposition'] || '';
                    let filename = row.origin_name;
                    const match = disposition.match(/filename\*=utf-8''([^;]+)/i);
                    if (match) filename = decodeURIComponent(match[1]);
                    const url = URL.createObjectURL(response.data);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = filename;
                    link.click();
                    URL.revokeObjectURL(url);
                } catch (error) {
                    MessagePlugin.error('模型下载失败');
                }
            };

            const confirmDelete = (row) => {
                const dialog = DialogPlugin.confirm({
                    header: '删除模型',
                    body: '确认删除模型“' + row.name + '”？模型文件将一并删除。',
                    theme: 'warning',
                    onConfirm: async () => {
                        try {
                            await api.delete('/models/' + row.id, requestConfig());
                            MessagePlugin.success('模型已删除');
                            await fetchModels();
                        } finally {
                            dialog.destroy();
                        }
                    }
                });
            };

            const loadedTabs = new Set();
            watch(activeTab, async (value) => {
                if (!isPageActive()) return;
                HuiYan.syncUrlTab(value);
                if (loadedTabs.has(value)) return;
                try {
                    if (value === 'records') {
                        await Promise.all([fetchRecordOptions(), fetchRecords()]);
                    } else {
                        await Promise.all([fetchModels(), fetchUploadPolicy()]);
                    }
                    loadedTabs.add(value);
                } catch (error) {
                    if (!isPageActive()) return;
                    // 统一请求层负责错误提示；保留未加载状态供用户切换后重试。
                }
            }, { immediate: true });

            return {
                activeTab,
                records, recordLoading, recordFilters, recordOptions,
                recordPagination, recordColumns, fetchRecords, searchRecords,
                resetRecords, onRecordPageChange, formatConfidence, formatBbox,
                recordDetailVisible, recordDetailLoading, recordDetail,
                recordDetailDetections, detectionColumns, openRecordDetail,
                models, loading, filters, pagination, columns,
                formatOptions, bindingStatusOptions, fetchModels, searchModels, resetModels, onPageChange,
                formatSize, shortHash, uploadAccept, uploadHint, uploadPolicyReady,
                modelDialogVisible, modelDialogTitle, modelFormRef, editingId, submitting,
                uploadFiles, modelForm, modelRules, openUpload, openEdit, submitModel,
                bindingDialogVisible, bindingLoading, bindingSubmitting, bindingAreas,
                bindingModel, bindingForm, openBindings, submitBindings,
                downloadModel, confirmDelete
            };
        }
    });
})();
