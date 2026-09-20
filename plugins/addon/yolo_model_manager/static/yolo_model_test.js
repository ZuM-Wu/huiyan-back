/* 智能识别模型测试弹窗与任务轮询。 */
(function () {
    const { ref, reactive, computed } = Vue;
    const { MessagePlugin } = TDesign;

    window.HuiYanYoloTest = {
        create({ api, request, requestConfig, isPageActive, unwrap, fetchRecordOptions,
            fetchRecords, recordDetail, recordDetailVisible }) {
            const imagePolicy = ref({ max_size_mb: 2, extensions: ['jpg', 'jpeg', 'png', 'gif', 'webp'] });
            const imageUploadAccept = computed(() => imagePolicy.value.extensions
                .map((extension) => '.' + extension).join(','));
            const imageUploadHint = computed(() => imagePolicy.value.extensions
                .map((item) => item.toUpperCase()).join(' / ') + '，最大 ' + imagePolicy.value.max_size_mb + 'MB');
            const modelTestVisible = ref(false);
            const modelTestSubmitting = ref(false);
            const modelTestFormRef = ref(null);
            const modelTestFiles = ref([]);
            const modelTestOptions = ref([]);
            const modelTestTask = ref(null);
            const modelTestForm = reactive({ model_id: null });
            const modelTestRules = {
                model_id: [{ required: true, message: '请选择识别模型', type: 'error' }],
                file: [{ validator: () => ({
                    result: modelTestFiles.value.length > 0, message: '请选择测试图片'
                }), type: 'error' }]
            };
            const fetchImagePolicy = async () => {
                const response = await request.get('/upload/settings', requestConfig());
                const data = unwrap(response);
                const policy = (data.policies || []).find((item) => item.id === 'core.image');
                if (policy) imagePolicy.value = policy;
            };
            const fetchModelTestOptions = async () => {
                const response = await api.get('/models', requestConfig({
                    params: { page: 1, limit: 100 }
                }));
                const data = unwrap(response);
                if (isPageActive()) {
                    modelTestOptions.value = (data.list || []).map((item) => ({
                        value: item.id,
                        label: (item.name + ' ' + (item.version || '')).trim()
                    }));
                }
            };
            const selectedFile = () => {
                const item = modelTestFiles.value[0];
                return item && (item.raw || item);
            };
            const openModelTest = () => {
                modelTestForm.model_id = null;
                modelTestFiles.value = [];
                modelTestTask.value = null;
                modelTestVisible.value = true;
                Promise.all([fetchRecordOptions(), fetchImagePolicy(), fetchModelTestOptions()])
                    .catch(() => {});
            };
            const pollModelTest = async (taskId) => {
                try {
                    while (isPageActive()) {
                        await new Promise((resolve) => window.setTimeout(resolve, 1000));
                        const response = await api.get('/recognitions/test-tasks/' + taskId, requestConfig());
                        const data = unwrap(response);
                        modelTestTask.value = data;
                        if (data.status === 'succeeded') {
                            modelTestVisible.value = false;
                            await fetchRecords();
                            recordDetail.value = data.record;
                            recordDetailVisible.value = true;
                            MessagePlugin.success('识别测试完成');
                            return;
                        }
                        if (['failed', 'cancelled'].includes(data.status)) {
                            MessagePlugin.error(data.message || '识别测试失败');
                            return;
                        }
                    }
                } catch (error) {
                    if (isPageActive()) {
                        MessagePlugin.error(error?.message || '查询识别测试状态失败');
                    }
                }
            };
            const submitModelTest = async () => {
                const valid = await modelTestFormRef.value?.validate();
                const file = selectedFile();
                if (valid !== true || !file) return;
                const extension = String(file.name || '').split('.').pop().toLowerCase();
                if (!imagePolicy.value.extensions.includes(extension)
                    || file.size > imagePolicy.value.max_size_mb * 1024 * 1024) {
                    MessagePlugin.error('图片格式或大小不符合上传设置');
                    return;
                }
                modelTestSubmitting.value = true;
                try {
                    const formData = new FormData();
                    formData.append('model_id', String(modelTestForm.model_id));
                    formData.append('file', file);
                    const response = await api.post('/recognitions/test-tasks', formData, requestConfig());
                    modelTestTask.value = unwrap(response);
                    MessagePlugin.success('识别测试已加入任务队列');
                    pollModelTest(modelTestTask.value.task_id);
                } finally {
                    modelTestSubmitting.value = false;
                }
            };
            return {
                modelTestVisible, modelTestSubmitting, modelTestFormRef, modelTestFiles,
                modelTestTask, modelTestForm, modelTestRules, modelTestOptions, imageUploadAccept,
                imageUploadHint, openModelTest, submitModelTest
            };
        }
    };
})();
