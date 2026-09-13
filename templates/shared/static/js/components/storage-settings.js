/* 存储设置状态：集中管理存储列表、切换及本地文件同步。 */
(function (global) {
    'use strict';

    function create(options) {
        const { ref, computed } = Vue;
        const request = options.request;
        const message = options.message;
        const storageLoading = ref(false);
        const storageList = ref([]);
        const currentStorageMethod = ref('');
        const storageMethod = ref('');
        const testingConnection = ref(false);
        const installingStoragePlugin = ref('');
        const switchDialogVisible = ref(false);
        const switchTargetMethod = ref('');
        const switchTargetName = ref('');
        const switchPassword = ref('');
        const switching = ref(false);
        const storageManager = ref(null);
        const migration = global.HuiYanStorageMigration.create({ request });
        const storageColumns = [
            { colKey: 'title', title: '插件名称', minWidth: 120 },
            { colKey: 'author', title: '开发者', minWidth: 100 },
            { colKey: 'version', title: '版本', width: 100 },
            { colKey: 'status', title: '状态', width: 100, cell: 'status' },
            { colKey: 'has_data', title: '是否存在数据', width: 120, cell: 'has_data' },
            { colKey: 'is_current', title: '当前使用', width: 100, cell: 'is_current' },
            { colKey: 'op', title: '操作', width: 260, cell: 'op', fixed: 'right' },
        ];
        const storageMethodOptions = computed(() => storageList.value
            .filter((item) => item.status === 1)
            .map((item) => ({ label: item.title, value: item.name })));
        const currentStorageMethodLabel = computed(() => {
            const found = storageList.value.find((item) => item.name === currentStorageMethod.value);
            return found ? found.title : currentStorageMethod.value;
        });
        const openStorageConfig = (row) => {
            if (storageManager.value?.openConfig) storageManager.value.openConfig(row);
        };
        const openStorageFiles = (row) => {
            if (storageManager.value?.openFiles) storageManager.value.openFiles(row);
        };
        const refreshAdminAuthCache = () => request.get('/permission/my-auth').then((res) => {
            const data = (res.data && res.data.data) || res.data || {};
            localStorage.setItem('admin_auth', JSON.stringify(data.auth || []));
            localStorage.setItem('admin_pages', JSON.stringify(data.pages || {}));
        });
        const installStoragePlugin = (row) => {
            if (!row || row.status !== 3 || installingStoragePlugin.value) return;
            installingStoragePlugin.value = row.name;
            request.post('/plugin/install/' + encodeURIComponent(row.name)).then(async (res) => {
                message.success((res.data && res.data.msg) || '存储插件安装成功');
                localStorage.removeItem('admin_menus');
                localStorage.removeItem('admin_auth');
                localStorage.removeItem('admin_pages');
                try {
                    await refreshAdminAuthCache();
                } catch (error) {
                    console.warn('[系统设置] OSS 插件安装后权限缓存刷新失败', error);
                }
                await fetchStorageList();
            }).catch((error) => {
                message.error((error.response && error.response.data && error.response.data.detail)
                    || '存储插件安装失败');
            }).finally(() => { installingStoragePlugin.value = ''; });
        };
        const fetchStorageList = () => {
            storageLoading.value = true;
            return request.get('/oss/list').then((res) => {
                const data = (res.data && res.data.data) || res.data || res;
                currentStorageMethod.value = data.current_method || 'local_oss';
                storageMethod.value = currentStorageMethod.value;
                storageList.value = (data.list || []).map((item) => ({
                    ...item, is_current: item.name === currentStorageMethod.value,
                }));
            }).catch(() => {}).finally(() => { storageLoading.value = false; });
        };
        const testConnection = () => {
            testingConnection.value = true;
            request.post('/oss/test').then((res) => {
                const data = (res.data && res.data.data) || res.data || res;
                if (data.status === 200 || data.success === true) {
                    message.success('连接成功：' + (data.msg || data.message || ''));
                } else {
                    message.error('连接失败：' + (data.msg || data.message || '未知错误'));
                }
            }).catch(() => {}).finally(() => { testingConnection.value = false; });
        };
        const openSwitchDialog = (row) => {
            switchTargetMethod.value = row.name;
            switchTargetName.value = row.title;
            switchPassword.value = '';
            switchDialogVisible.value = true;
        };
        const openSwitchDialogFromSelect = () => {
            if (storageMethod.value === currentStorageMethod.value) {
                message.info('存储方式未变更');
                return;
            }
            const target = storageList.value.find((item) => item.name === storageMethod.value);
            switchTargetMethod.value = storageMethod.value;
            switchTargetName.value = target ? target.title : storageMethod.value;
            switchPassword.value = '';
            switchDialogVisible.value = true;
        };
        const confirmSwitch = () => {
            if (!switchPassword.value) {
                message.warning('请输入管理员密码');
                return;
            }
            switching.value = true;
            request.put('/oss/switch', {
                oss_method: switchTargetMethod.value, password: switchPassword.value,
            }).then(() => {
                message.success('存储方式已切换');
                switchDialogVisible.value = false;
                return fetchStorageList();
            }).catch(() => {}).finally(() => { switching.value = false; });
        };
        return {
            storageLoading, storageList, currentStorageMethodLabel, storageMethod, storageMethodOptions,
            installingStoragePlugin, storageColumns, testingConnection, testConnection, switchDialogVisible,
            switchTargetName, switchPassword, switching, openSwitchDialog, openSwitchDialogFromSelect,
            confirmSwitch, installStoragePlugin, storageManager, openStorageConfig, openStorageFiles,
            ...migration, fetchStorageList,
        };
    }

    global.HuiYanStorageSettings = { create };
})(window);
