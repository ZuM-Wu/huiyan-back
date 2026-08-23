/** 推送中心任务创建/编辑逻辑。 */
(function () {
    const { ref, reactive, computed, onMounted, nextTick } = Vue;
    const { MessagePlugin } = TDesign;
    const editId = Number(new URLSearchParams(location.search).get('id')) || 0;

    const unwrap = (response) => response?.data?.data ?? response?.data ?? {};
    const errorMessage = (error, fallback) => error?.response?.data?.msg || error?.response?.data?.detail || fallback;

    HuiYan.createPluginPage({ plugin: 'push', page: 'push-task-form',
        setup() {
            const formRef = ref(null);
            const pageLoading = ref(false);
            const submitting = ref(false);
            const pushEditor = ref(null);
            const formData = reactive({
                title: '', keywords: '', content: '', subject: '',
                channels: {
                    inbox: true, sms: false, email: false,
                    sms_interface: '', sms_template_id: 0, email_template_id: 0,
                },
                target_rule: { mode: 'all', farmer_ids: [], filters: {} },
                schedule_rule: {
                    cycle: 'onetime', week_day: 1, month_day: 1,
                    time_hour_min: '09:00', start_time: null, end_time: null,
                },
            });
            const channelValues = computed({
                get: () => ['inbox', 'sms', 'email'].filter((name) => formData.channels[name]),
                set: (values) => {
                    ['inbox', 'sms', 'email'].forEach((name) => { formData.channels[name] = values.includes(name); });
                },
            });
            const channelOptions = [
                { label: '站内信', value: 'inbox' },
                { label: '短信', value: 'sms' },
                { label: '邮件', value: 'email' },
            ];
            const filterConditions = ref([]);
            const filterKeyword = ref('');
            const filterArea = ref('');
            const targetCount = ref(0);
            const farmerOptions = ref([]);
            const farmerOptionsLoading = ref(false);
            const smsPlugins = ref([]);
            const smsTemplates = ref([]);
            const emailTemplates = ref([]);
            const selectedSmsPlugin = ref('');
            const targetDialogVisible = ref(false);
            const targetPreviewList = ref([]);
            const targetPreviewLoading = ref(false);
            const targetPagination = reactive({ current: 1, pageSize: 100, total: 0 });
            const targetColumns = [
                { colKey: 'id', title: 'ID', width: 60 },
                { colKey: 'username', title: '用户名', width: 140 },
                { colKey: 'phone', title: '手机号', width: 140 },
                { colKey: 'email', title: '邮箱', ellipsis: true },
            ];
            const previewVisible = ref(false);
            const previewForm = reactive({ email: '', phone: '' });
            const previewSending = ref(false);
            const isEdit = computed(() => editId > 0);

            const formRules = {
                title: [{ required: true, message: '请输入推送标题', type: 'error' }],
                content: [{ validator: () => ({
                    result: !!(pushEditor.value?.getContent() || formData.content).trim(),
                    message: '请输入推送内容', type: 'error',
                }) }],
                channels: [{ validator: () => ({
                    result: channelValues.value.length > 0,
                    message: '至少选择一个推送渠道', type: 'error',
                }) }],
                target_rule: [{ validator: () => ({
                    result: formData.target_rule.mode !== 'specified' || formData.target_rule.farmer_ids.length > 0,
                    message: '请选择至少一个农户', type: 'error',
                }) }],
            };

            const syncTargetFilters = () => {
                formData.target_rule.filters = {
                    has_phone: filterConditions.value.includes('has_phone'),
                    has_email: filterConditions.value.includes('has_email'),
                    has_plot: filterConditions.value.includes('has_plot'),
                    keyword: filterKeyword.value.trim(),
                    area_name: filterArea.value.trim(),
                };
            };
            const syncEditor = () => { if (pushEditor.value) formData.content = pushEditor.value.getContent(); };
            const goBack = () => { location.href = '/admin/plugin/push/notice-push'; };
            const weekDayLabel = (value) => ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日'][value] || '';

            const loadFarmerOptions = async () => {
                farmerOptionsLoading.value = true;
                try { farmerOptions.value = unwrap(await request.get('/farmer/options')); }
                catch (error) { MessagePlugin.error(errorMessage(error, '农户列表加载失败')); }
                finally { farmerOptionsLoading.value = false; }
            };
            const loadPlugins = async () => {
                try {
                    const response = await request.get('/notice/interfaces/list', { params: { module: 'sms' } });
                    const data = response?.data?.data ?? response?.data ?? {};
                    smsPlugins.value = (data.list || data || []).filter((item) => item.installed);
                } catch (_) { smsPlugins.value = []; }
            };
            const loadSmsTemplates = async () => {
                try {
                    const data = unwrap(await request.get('/notice/sms-templates/list', { params: { page: 1, limit: 100, interface: selectedSmsPlugin.value || undefined } }));
                    smsTemplates.value = data.list || [];
                } catch (_) { smsTemplates.value = []; }
            };
            const loadEmailTemplates = async () => {
                try {
                    const data = unwrap(await request.get('/notice/email-templates/list', { params: { page: 1, limit: 100 } }));
                    emailTemplates.value = data.list || [];
                } catch (_) { emailTemplates.value = []; }
            };
            const loadTemplates = async () => Promise.all([loadSmsTemplates(), loadEmailTemplates()]);
            const onSmsPluginChange = () => { formData.channels.sms_template_id = 0; loadSmsTemplates(); };
            const onEmailTemplateChange = async (templateId) => {
                if (!templateId) return;
                let template = emailTemplates.value.find((item) => item.id === templateId);
                if (!template?.content) {
                    try { template = unwrap(await request.get('/notice/email-templates/' + templateId)); } catch (_) { return; }
                }
                if (template.subject) formData.subject = template.subject;
                if (template.content && pushEditor.value) nextTick(() => pushEditor.value.setContent(template.content));
            };

            const loadTask = async () => {
                pageLoading.value = true;
                try {
                    const task = unwrap(await request.get(`/push/tasks/${editId}`));
                    Object.assign(formData, task);
                    formData.channels = { ...formData.channels };
                    formData.target_rule = { mode: 'all', farmer_ids: [], filters: {}, ...formData.target_rule };
                    formData.schedule_rule = { cycle: 'onetime', week_day: 1, month_day: 1, time_hour_min: '09:00', ...formData.schedule_rule };
                    const filters = formData.target_rule.filters || {};
                    filterConditions.value = ['has_phone', 'has_email', 'has_plot'].filter((key) => filters[key]);
                    filterKeyword.value = filters.keyword || '';
                    filterArea.value = filters.area_name || '';
                    targetCount.value = task.target_count || 0;
                    await nextTick();
                    if (pushEditor.value && formData.content) pushEditor.value.setContent(formData.content);
                } catch (error) { MessagePlugin.error(errorMessage(error, '加载任务失败')); }
                finally { pageLoading.value = false; }
            };

            const previewTarget = async () => {
                if (formData.target_rule.mode === 'filtered') syncTargetFilters();
                if (formData.target_rule.mode === 'specified' && !formData.target_rule.farmer_ids.length) {
                    MessagePlugin.warning('请选择至少一个农户'); return;
                }
                targetPreviewLoading.value = true;
                targetDialogVisible.value = true;
                try {
                    const data = unwrap(await request.post('/push/tasks/target-preview', {
                        target_rule: formData.target_rule,
                        page: targetPagination.current,
                        limit: targetPagination.pageSize,
                    }));
                    targetPreviewList.value = data.list || [];
                    targetCount.value = data.total || 0;
                    targetPagination.total = data.total || 0;
                    if (!targetCount.value) MessagePlugin.warning('暂无符合条件的活跃农户');
                } catch (error) {
                    targetDialogVisible.value = false;
                    MessagePlugin.error(errorMessage(error, '匹配名单预览失败'));
                } finally { targetPreviewLoading.value = false; }
            };
            const onTargetPageChange = (info) => { targetPagination.current = info.current; previewTarget(); };
            const submitPreview = async () => {
                if (!previewForm.email.trim() && !previewForm.phone.trim()) { MessagePlugin.warning('请填写邮箱或手机号'); return; }
                previewSending.value = true;
                try {
                    syncEditor();
                    if (!isEdit.value) { MessagePlugin.warning('请先保存任务后再发送预览'); return; }
                    const data = unwrap(await request.post(`/push/tasks/${editId}/preview-send`, previewForm));
                    MessagePlugin.success(data.msg || '预览发送完成'); previewVisible.value = false;
                } catch (error) { MessagePlugin.error(errorMessage(error, '预览发送失败')); }
                finally { previewSending.value = false; }
            };
            const showPreview = () => {
                if (!isEdit.value) { MessagePlugin.warning('请先保存任务后再发送预览'); return; }
                previewForm.email = '';
                previewForm.phone = '';
                previewVisible.value = true;
            };
            const onFormSubmit = async ({ validateResult }) => {
                syncEditor();
                if (formData.target_rule.mode === 'filtered') syncTargetFilters();
                if (validateResult !== true) return;
                submitting.value = true;
                try {
                    const payload = JSON.parse(JSON.stringify(formData));
                    const response = isEdit.value
                        ? await request.patch(`/push/tasks/${editId}`, payload)
                        : await request.post('/push/tasks', payload);
                    if (response.status === 200) { MessagePlugin.success(isEdit.value ? '更新成功' : '创建成功'); setTimeout(goBack, 500); }
                } catch (error) { MessagePlugin.error(errorMessage(error, '保存失败')); }
                finally { submitting.value = false; }
            };

            onMounted(async () => {
                document.title = (isEdit.value ? '编辑' : '创建') + '推送任务 - 慧眼护农';
                await Promise.all([loadPlugins(), loadTemplates(), loadFarmerOptions()]);
                if (isEdit.value) await loadTask();
            });
            return {
                isEdit, formRef, pageLoading, submitting, pushEditor, formData, formRules,
                channelValues, channelOptions, filterConditions, filterKeyword, filterArea,
                targetCount, farmerOptions, farmerOptionsLoading, smsPlugins, smsTemplates, emailTemplates,
                selectedSmsPlugin, targetDialogVisible, targetPreviewList, targetPreviewLoading,
                targetPagination, targetColumns, previewVisible, previewForm, previewSending,
                goBack, weekDayLabel, onSmsPluginChange, onEmailTemplateChange, previewTarget,
                onTargetPageChange, showPreview, submitPreview, onFormSubmit,
            };
        },
    });
})();
