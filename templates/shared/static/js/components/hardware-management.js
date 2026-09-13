/** 独立物联网管理页：复用公共设备状态，只增加配置与慧眼注册流程。 */
(function (window) {
    'use strict';
    window.HuiYanHardwareManagement = {
        setup({ plugin, api, request }) {
            const { ref, reactive, computed, watch, onBeforeUnmount } = Vue;
            const { MessagePlugin } = TDesign;
            const controller = new AbortController();
            let disposed = false, detailGeneration = 0;
            const isHuiyan = plugin === 'hardware_huiyan';
            const devices = window.HuiYanHardwareDevices.setup({ providerId: plugin, request });
            const viewStorageKey = 'hardware_management_view:' + plugin;
            const viewMode = ref('cards');
            try {
                if (localStorage.getItem(viewStorageKey) === 'list') viewMode.value = 'list';
            } catch (_) { /* 浏览器禁用存储时仍可切换，本次进入使用卡片。 */ }
            watch(viewMode, (value) => {
                try { localStorage.setItem(viewStorageKey, value); } catch (_) { /* 不阻断当前页面操作。 */ }
            });
            const changeViewPage = (info) => devices.onPageChange(info);
            const cardName = (row) => row.nickname || (isHuiyan ? row.device_name : row.device_type_label) || '未命名设备';
            const cardCode = (row) => isHuiyan ? row.provider_device_id : row.device_name;
            const failedCardImages = reactive({});
            const cardImageFailed = (row) => { failedCardImages[row.id] = row.image_url; };
            const authCodes = HuiYan.getAuthCodes();
            const canConfigure = authCodes.includes('plugin:config');
            const canSchedule = authCodes.includes('hardware:list') || authCodes.includes('hardware:*')
                || authCodes.includes('*');
            const configLoading = ref(false), configSaving = ref(false), configError = ref('');
            const configLoaded = ref(false);
            const configForm = reactive({ base_url: '', credential: '', credential_configured: false });
            const loadConfig = async () => {
                configLoading.value = true; configError.value = '';
                try {
                    const data = (await api.get('/config', { signal: controller.signal })).data.data;
                    if (disposed) return;
                    Object.assign(configForm, data, { credential: '' }); configLoaded.value = true;
                } catch (_) { if (!disposed) configError.value = '配置加载失败，请重试'; }
                finally { configLoading.value = false; }
            };
            const saveConfig = async () => {
                configSaving.value = true;
                try {
                    const payload = { credential: configForm.credential };
                    if (!isHuiyan) payload.base_url = configForm.base_url;
                    const data = (await api.put('/config', payload, { signal: controller.signal })).data.data;
                    if (disposed) return;
                    Object.assign(configForm, data, { credential: '' });
                    MessagePlugin.success('配置已保存');
                } catch (_) { /* 公共请求层展示脱敏错误。 */ }
                finally { configSaving.value = false; }
            };
            const onPageTabChange = (value) => {
                if (value === 'config' && !isHuiyan && canConfigure && !configLoaded.value) loadConfig();
                if (value === 'schedule' && !devices.settingsLoaded.value) devices.fetchAutoFetchSettings();
            };
            const registerVisible = ref(false), registering = ref(false);
            const registerError = ref(''), registrationPendingSync = ref(false), registerFormRef = ref(null);
            const registeredDeviceId = ref('');
            const copyDeviceId = async (value) => {
                try { await navigator.clipboard.writeText(value); MessagePlugin.success('设备 ID 已复制'); }
                catch (_) { MessagePlugin.warning('复制失败，请选中设备 ID 手动复制'); }
            };
            const emptyForm = () => ({ device_name: '', device_type: '', nickname: '', address: '' });
            const registerForm = reactive(emptyForm());
            const registerRules = {
                device_name: [{ required: true, message: '请输入设备名称', trigger: 'blur' }],
            };
            const openRegister = () => {
                Object.assign(registerForm, emptyForm()); registerError.value = '';
                registerVisible.value = true;
            };
            const retryRegistrationSync = async () => {
                registrationPendingSync.value = !(await devices.syncDevices());
            };
            const submitRegistration = async ({ validateResult } = {}) => {
                if (validateResult !== true || registering.value) return;
                registering.value = true; registerError.value = '';
                const type = devices.deviceTypeOptions.value.find((item) => item.value === registerForm.device_type);
                try {
                    // 管理页只创建新设备；ID交给服务端生成，位置只收集选填安装地址。
                    const payload = Object.fromEntries(Object.entries(registerForm).filter(([, value]) => value !== ''));
                    if (registerForm.device_type) payload.device_type_label = type?.label || registerForm.device_type;
                    const response = await api.post('/devices/register', payload,
                        { signal: controller.signal, skipAutoError: true });
                    if (disposed) return;
                    registeredDeviceId.value = response.data.data.device_id;
                    registerVisible.value = false;
                    MessagePlugin.success('设备注册成功');
                    registrationPendingSync.value = true;
                    await retryRegistrationSync();
                } catch (error) {
                    if (disposed) return;
                    registerError.value = error.response?.data?.msg || error.response?.data?.detail || '注册失败，请检查输入后重试';
                    if (typeof registerError.value !== 'string') registerError.value = '注册参数校验失败，请检查设备名称';
                } finally { registering.value = false; }
            };
            const deleteVisible = ref(false), deleting = ref(false), deleteTarget = ref(null), deleteError = ref('');
            const isDeviceBound = (row) => row?.area_id != null || row?.plot_id != null;
            const openDelete = (row) => {
                if (!isHuiyan || deleting.value || isDeviceBound(row)) return;
                deleteTarget.value = { ...row }; deleteError.value = ''; deleteVisible.value = true;
            };
            const submitDelete = async () => {
                if (deleting.value || !deleteTarget.value) return;
                deleting.value = true; deleteError.value = '';
                const target = deleteTarget.value;
                try {
                    await api.delete('/devices/' + encodeURIComponent(target.provider_device_id),
                        { signal: controller.signal, skipAutoError: true });
                    if (disposed) return;
                    if (devices.currentDevice.value?.id === target.id) {
                        devices.closeDrawer(); devices.currentDevice.value = null;
                    }
                    if (registeredDeviceId.value === target.provider_device_id) {
                        registeredDeviceId.value = ''; registrationPendingSync.value = false;
                    }
                    deleteVisible.value = false; deleteTarget.value = null;
                    MessagePlugin.success('设备已删除');
                    await devices.fetchDevices();
                    const lastPage = Math.max(1, Math.ceil(devices.pagination.total / devices.pagination.pageSize));
                    if (!disposed && devices.pagination.current > lastPage) {
                        devices.pagination.current = lastPage; await devices.fetchDevices();
                    }
                } catch (error) {
                    if (disposed) return;
                    const message = error.response?.data?.detail || error.response?.data?.msg;
                    deleteError.value = typeof message === 'string' ? message : '删除失败，请重试';
                } finally { deleting.value = false; }
            };
            const managementColumns = devices.columns.map((column) => column.colKey === 'operation' && isHuiyan
                ? { ...column, width: 160 } : column);
            const registrationDetail = ref(null), registrationDetailError = ref(''), registrationDetailLoading = ref(false);
            const registrationJson = computed(() => {
                let value = registrationDetail.value?.metadata;
                if (value == null || value === '') return '';
                if (typeof value === 'string') {
                    try { value = JSON.parse(value); } catch (_) { return value; }
                }
                return JSON.stringify(value, null, 2);
            });
            const copyRegistrationJson = async () => {
                try { await navigator.clipboard.writeText(registrationJson.value); MessagePlugin.success('扩展信息已复制'); }
                catch (_) { MessagePlugin.warning('复制失败，请选中内容手动复制'); }
            };
            const loadRegistrationDetail = async () => {
                const row = devices.currentDevice.value, generation = ++detailGeneration;
                registrationDetail.value = null; registrationDetailError.value = ''; registrationDetailLoading.value = false;
                if (!isHuiyan || !row || !devices.drawerVisible.value) return;
                registrationDetailLoading.value = true;
                try {
                    const data = (await api.get('/devices/' + encodeURIComponent(row.provider_device_id), { signal: controller.signal })).data.data;
                    if (!disposed && generation === detailGeneration) registrationDetail.value = data;
                } catch (_) {
                    if (!disposed && generation === detailGeneration) registrationDetailError.value = '注册详情加载失败';
                } finally {
                    if (!disposed && generation === detailGeneration) registrationDetailLoading.value = false;
                }
            };
            watch([devices.currentDevice, devices.drawerVisible], loadRegistrationDetail);
            onBeforeUnmount(() => { disposed = true; detailGeneration += 1; controller.abort(); configForm.credential = ''; });
            return { ...devices, isHuiyan, canConfigure, canSchedule, configForm, configLoading, configSaving, configError,
                configLoaded, loadConfig, saveConfig, onPageTabChange, registerVisible, registering,
                registerError, registerForm, registerFormRef, registerRules, openRegister, submitRegistration,
                registrationPendingSync, retryRegistrationSync, registrationDetail, registrationDetailError,
                registeredDeviceId, copyDeviceId, registrationDetailLoading, loadRegistrationDetail,
                registrationJson, copyRegistrationJson, deleteVisible, deleting, deleteTarget, deleteError,
                isDeviceBound, openDelete, submitDelete, managementColumns,
                viewMode, changeViewPage, cardName, cardCode, failedCardImages, cardImageFailed };
        },
    };
})(window);
