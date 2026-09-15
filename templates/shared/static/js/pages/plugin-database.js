(function () {
    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    const statusLabels = Object.freeze({
        current: '正常',
        version_mismatch: '版本不一致',
        local_newer: '安装版较新',
        schema_mismatch: '结构不一致',
        missing_files: '文件缺失',
        invalid_manifest: '清单异常',
        not_installed: '未安装',
        unverified: '待核验',
        awaiting_restart: '待重启生效',
    });
    const schemaStatusLabels = Object.freeze({
        current: '正常',
        not_applicable: '不涉及',
        mismatch: '结构不一致',
        unverified: '待核验',
    });

    HuiYan.createPage({
        setup() {
            const rows = ref([]);
            const loading = ref(false);
            const scanning = ref(false);
            const keyword = ref('');
            const module = ref('');
            const status = ref('');
            const detailVisible = ref(false);
            const detailReport = ref('');
            const configVisible = ref(false);
            const config = reactive({ enabled: true, interval_hours: 6 });
            const pagination = reactive({ current: 1, pageSize: 20, total: 0, showJumper: false });
            const columns = [
                { colKey: 'plugin_name', title: '插件', minWidth: 220, cell: 'plugin_name' },
                { colKey: 'module', title: '模块', width: 120 },
                { colKey: 'db_version', title: '数据库版本', width: 120 },
                { colKey: 'disk_version', title: '文件版本', width: 120 },
                { colKey: 'schema_status', title: '结构状态', width: 120, cell: 'schema_status' },
                { colKey: 'status', title: '运行状态', width: 140, cell: 'status' },
                { colKey: 'last_scan_at', title: '最近扫描', width: 180, cell: 'last_scan_at' },
                { colKey: 'op', title: '操作', width: 280, cell: 'op' },
            ];
            const statusOptions = Object.entries(statusLabels).map(([value, label]) => ({ label, value }));

            // 状态值由后端保存英文枚举，页面统一转换为中文，避免管理员直接阅读内部标识。
            const statusLabel = value => statusLabels[value] || value || '未知';
            const schemaStatusLabel = value => schemaStatusLabels[value] || value || '未知';
            const statusTheme = value => value === 'current' ? 'success' : 'warning';
            const schemaStatusTheme = value => value === 'current' || value === 'not_applicable' ? 'success' : 'warning';
            const formatScanTime = value => value ? String(value).replace('T', ' ').replace(/\.\d+$/, '') : '-';

            // 查询时回到第一页；翻页时保留筛选条件并只更新当前页。
            const load = () => {
                loading.value = true;
                return request.get('/plugin-database/status', {
                    params: {
                        page: pagination.current,
                        limit: pagination.pageSize,
                        keyword: keyword.value.trim(),
                        module: module.value.trim(),
                        status: status.value,
                    },
                }).then(res => {
                    const data = res.data.data || res.data || {};
                    rows.value = data.list || [];
                    pagination.total = data.total || 0;
                }).finally(() => {
                    loading.value = false;
                });
            };
            const query = () => {
                pagination.current = 1;
                return load();
            };
            const resetFilter = () => {
                keyword.value = '';
                module.value = '';
                status.value = '';
                return query();
            };
            const onPageChange = pageInfo => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                return load();
            };
            const scan = () => {
                scanning.value = true;
                request.post('/plugin-database/scan').then(() => MessagePlugin.success('扫描任务已提交')).finally(() => {
                    scanning.value = false;
                });
            };
            const showDetail = row => {
                detailVisible.value = true;
                detailReport.value = JSON.stringify({ ...row, report: row.report || {} }, null, 2);
            };
            const prepareRepair = row => request.post(`/plugin-database/${row.plugin_name}/repair/prepare`)
                .then(() => MessagePlugin.success('修复计划已生成')).catch(() => {});
            const prepareUpgrade = row => request.post('/plugin-database/prepare-batch', { names: [row.plugin_name] })
                .then(() => MessagePlugin.success('升级计划已生成')).catch(() => {});
            const openControlCenter = row => {
                window.open(`http://127.0.0.1:8765/?operation_id=${encodeURIComponent(row.pending_operation_id || '')}`, '_blank', 'noopener');
            };
            const loadConfig = () => {
                configVisible.value = true;
                request.get('/plugin-database/config').then(res => Object.assign(config, res.data.data || res.data || {}));
            };
            const saveConfig = () => request.put('/plugin-database/config', config).then(() => {
                MessagePlugin.success('扫描配置已保存');
                configVisible.value = false;
            }).catch(() => {});

            onMounted(load);
            return {
                rows, loading, scanning, keyword, module, status, pagination, columns, statusOptions,
                detailVisible, detailReport, configVisible, config,
                statusLabel, schemaStatusLabel, statusTheme, schemaStatusTheme, formatScanTime,
                load, query, resetFilter, onPageChange, scan, showDetail, prepareRepair,
                prepareUpgrade, openControlCenter, loadConfig, saveConfig,
            };
        },
    });
})();
