/** 硬件设备管理页面。 */
(function () {
    'use strict';

    const { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } = Vue;
    const { DialogPlugin, MessagePlugin } = TDesign;

    window.HuiYanHardwareDevices = {
        setup(options = {}) {
            const request = options.request || window.request;
            const providerId = options.providerId || '';
            const pageTab = ref('devices');
            const loading = ref(false);
            const syncing = ref(false);
            const deviceList = ref([]);
            const filters = reactive({ providerId, keyword: '', deviceType: '', bindingStatus: '', available: '' });
            const pagination = reactive({ current: 1, pageSize: 20, total: 0 });
            const providers = ref([]);
            const providerOptions = computed(() => providers.value.map((item) => ({ label: item.title, value: item.provider_id })));
            const deviceTypeOptions = computed(() => Array.from(new Map(providers.value
                .filter((item) => !filters.providerId || item.provider_id === filters.providerId)
                .flatMap((item) => item.device_types || []).map((item) => [item.value, item])).values()));
            const fetchProviders = () => request.get('/hardware-devices/providers').then((res) => { providers.value = res.data.data.list; }).catch(() => {});
            const bindingOptions = [{ label: '已绑定', value: 'bound' }, { label: '未绑定', value: 'unbound' }];
            const availableOptions = [{ label: '可用', value: 1 }, { label: '不可用', value: 0 }];
            const columns = [{ colKey: 'device', title: '设备', minWidth: 260, cell: 'device' },
                { colKey: 'provider_title', title: '来源', width: 130 },
                { colKey: 'type', title: '类型', width: 150, cell: 'type' }, { colKey: 'available', title: '平台状态', width: 100, cell: 'available' }, { colKey: 'binding', title: '绑定位置', minWidth: 180, cell: 'binding' }, { colKey: 'last_sync_time', title: '最近同步', width: 180, cell: 'last_sync_time' }, { colKey: 'operation', title: '操作', width: 90, fixed: 'right', cell: 'operation' }];

            const deviceIcon = (type) => ({ growth: 'image', soil: 'chart-bubble', root: 'tree-round-dot' }[type] || 'control-platform');
            const timeParts = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
            const formatChinaTime = (value) => {
                if (!value) return '暂无';
                const date = new Date(value);
                if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ');
                const parts = {};
                timeParts.formatToParts(date).forEach((item) => { parts[item.type] = item.value; });
                return parts.year + '-' + parts.month + '-' + parts.day + ' ' + parts.hour + ':' + parts.minute + ':' + parts.second;
            };
            const requestParams = () => {
                const params = { provider_id: providerId || filters.providerId, keyword: filters.keyword, device_type: filters.deviceType,
                    binding_status: filters.bindingStatus, page: pagination.current,
                    size: pagination.pageSize };
                if (filters.available === 0 || filters.available === 1) params.available = filters.available;
                return params;
            };
            const fetchDevices = () => {
                loading.value = true;
                return request.get('/hardware-devices', { params: requestParams() }).then((res) => {
                    const data = res.data.data || {};
                    deviceList.value = data.list || [];
                    pagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { loading.value = false; });
            };
            const search = () => { pagination.current = 1; fetchDevices(); };
            const onPageChange = (info) => { pagination.current = info.current;
                pagination.pageSize = info.pageSize; fetchDevices(); };
            const syncError = ref('');
            const syncDevices = async () => {
                syncing.value = true; syncError.value = '';
                try {
                    const res = await request.post('/hardware-devices/sync', null, { params: { provider_id: providerId || filters.providerId } });
                    const data = res.data.data || {};
                    const failed = (data.providers || []).filter((item) => !item.success);
                    if (data.skipped || failed.length) {
                        syncError.value = failed.map((item) => item.error || '来源同步失败').join('；') || '没有可同步的来源';
                        return false;
                    }
                    MessagePlugin.success('同步完成：获取 ' + (data.received || 0) + ' 台设备');
                    await fetchDevices(); return true;
                } catch (_) { syncError.value = '同步失败，请重试'; return false; }
                finally { syncing.value = false; }
            };

            const settingsLoading = ref(false), settingsSaving = ref(false), settingsLoaded = ref(false);
            const settingsError = ref('');
            const autoFetchSettings = reactive({ enabled: true, intervalSeconds: 900 });
            const realtimeRefreshTick = ref(0);
            let realtimeRefreshTimer = null;
            const stopRealtimeRefresh = () => {
                if (realtimeRefreshTimer !== null) {
                    window.clearInterval(realtimeRefreshTimer);
                    realtimeRefreshTimer = null;
                }
            };
            const startRealtimeRefresh = () => {
                stopRealtimeRefresh();
                if (!autoFetchSettings.enabled || !drawerVisible.value) return;
                const interval = Math.max(1, Number(autoFetchSettings.intervalSeconds) || 900);
                realtimeRefreshTimer = window.setInterval(() => {
                    if (drawerVisible.value) realtimeRefreshTick.value += 1;
                }, interval * 1000);
            };
            const fetchAutoFetchSettings = () => {
                settingsLoading.value = true; settingsError.value = '';
                return request.get('/hardware-devices/auto-fetch-settings').then((res) => {
                    const data = res.data.data || {};
                    autoFetchSettings.enabled = data.enabled !== false;
                    autoFetchSettings.intervalSeconds = data.interval_seconds
                        || ((data.interval_minutes || 15) * 60);
                    settingsLoaded.value = true;
                    startRealtimeRefresh();
                }).catch(() => { settingsError.value = '调度设置加载失败，请重试';
                }).finally(() => { settingsLoading.value = false; });
            };
            const saveAutoFetchSettings = () => {
                settingsSaving.value = true;
                request.put('/hardware-devices/auto-fetch-settings', {
                    enabled: autoFetchSettings.enabled,
                    interval_seconds: autoFetchSettings.intervalSeconds,
                }).then((res) => {
                    const data = res.data.data || {};
                    autoFetchSettings.enabled = data.enabled !== false;
                    autoFetchSettings.intervalSeconds = data.interval_seconds
                        || ((data.interval_minutes || 15) * 60);
                    startRealtimeRefresh();
                    MessagePlugin.success(res.data.msg || '自动获取设置已保存并生效');
                }).catch(() => { settingsError.value = '调度设置保存失败，请重试';
                }).finally(() => { settingsSaving.value = false; });
            };
            const onPageTabChange = (value) => { if (value === 'schedule' && !settingsLoaded.value) fetchAutoFetchSettings(); };

            const drawerVisible = ref(false), currentDevice = ref(null);
            const quickDetection = HuiYanHardwareQuickDetect.create({ request, currentDevice, drawerVisible });
            const detailTab = ref('current');
            const realtimeRefreshEnabled = computed(() => autoFetchSettings.enabled);
            const detailHost = HuiYanHardwareDetail.create({
                request, currentDevice, drawerVisible, quickDetection, formatTime: formatChinaTime,
                realtimeRefreshTick, realtimeRefreshEnabled,
            });
            const drawerTitle = computed(() => currentDevice.value
                ? (currentDevice.value.nickname || currentDevice.value.device_name) : '设备详情');
            const appearance = reactive({ imageUrl: '' }), binding = reactive({ areaId: null, plotId: null }), areaTree = ref([]);
            const areaOptions = computed(() => areaTree.value.filter((item) => item.status === 1)
                .map((item) => ({ label: item.name, value: item.id })));
            const plotOptions = computed(() => {
                const area = areaTree.value.find((item) => item.id === binding.areaId);
                return area ? (area.children || []).filter((item) => item.status === 1).map((item) => ({
                    label: item.name, value: item.id,
                })) : [];
            });
            const fetchAreaTree = () => request.get('/production-area/tree')
                .then((res) => { areaTree.value = res.data.data || []; }).catch(() => {});
            const openDetail = (row) => {
                currentDevice.value = Object.assign({}, row);
                appearance.imageUrl = row.image_url || '';
                binding.areaId = row.area_id || null;
                binding.plotId = row.plot_id || null;
                detailTab.value = 'current';
                drawerVisible.value = true;
                if (!areaTree.value.length) fetchAreaTree();
                detailHost.open(row);
                quickDetection.refreshForDevice(currentDevice.value);
                startRealtimeRefresh();
            };
            const closeDrawer = () => {
                drawerVisible.value = false; stopRealtimeRefresh(); quickDetection.dispose(); detailHost.dispose();
            };
            const onAreaChange = () => { binding.plotId = null; };
            const saveAppearance = () => request.patch(
                '/hardware-devices/' + currentDevice.value.id + '/appearance',
                { image_url: appearance.imageUrl || '' }
            ).then((res) => {
                currentDevice.value.image_url = appearance.imageUrl || '';
                MessagePlugin.success(res.data.msg || '设备图片已更新');
                fetchDevices();
            }).catch(() => {});
            const saveBinding = () => request.put(
                '/hardware-devices/' + currentDevice.value.id + '/binding',
                { area_id: binding.areaId, plot_id: binding.plotId }
            ).then((res) => {
                MessagePlugin.success(res.data.msg || '设备绑定已更新');
                drawerVisible.value = false;
                fetchDevices();
            }).catch(() => {});
            const removeBinding = () => {
                const dialog = DialogPlugin.confirm({
                    header: '解除设备绑定', body: '解除后，该设备将不再显示在地块边界上。', theme: 'warning',
                    onConfirm: () => request.delete('/hardware-devices/' + currentDevice.value.id + '/binding').then((res) => {
                        MessagePlugin.success(res.data.msg || '设备已解绑');
                        dialog.destroy(); drawerVisible.value = false; fetchDevices();
                    }).catch(() => { dialog.destroy(); }),
                    onClose: () => { dialog.destroy(); },
                });
            };

            onMounted(() => {
                quickDetection.initialize(); fetchProviders(); fetchDevices(); fetchAreaTree(); fetchAutoFetchSettings();
            });
            onBeforeUnmount(() => { stopRealtimeRefresh(); quickDetection.dispose(); detailHost.dispose(); });
            return {
                fetchDevices, syncError, pageTab, loading, syncing, deviceList, filters, pagination, columns, deviceTypeOptions, bindingOptions, availableOptions, deviceIcon, search, onPageChange, syncDevices, formatChinaTime,
                autoFetchSettings, settingsLoading, settingsSaving, settingsError, settingsLoaded,
                saveAutoFetchSettings, onPageTabChange, realtimeRefreshTick,
                drawerVisible, drawerTitle, currentDevice, detailTab, appearance, binding, areaOptions, plotOptions, openDetail, closeDrawer, onAreaChange, saveAppearance, saveBinding, removeBinding,
                ...detailHost.bindings, providers, providerOptions,
                ...quickDetection.bindings,
            };
        },
    };
    if (!document.currentScript?.src.includes('library=1') && !document.getElementById('plugin-page-context')) {
        HuiYan.createPage({ setup: () => window.HuiYanHardwareDevices.setup({ request }) });
    }
})();
