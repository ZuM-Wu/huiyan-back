(function () {
    const { ref, reactive, computed, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;
    HuiYan.createPage({
        setup() {
            const rows = ref([]), loading = ref(false), scanning = ref(false), total = ref(0), page = ref(1), limit = 20;
            const keyword = ref(''), module = ref(''), status = ref('');
            const detailVisible = ref(false), detailReport = ref(''), configVisible = ref(false);
            const config = reactive({ enabled: true, interval_hours: 6 });
            const summary = ref({});
            const columns = [
                { colKey: 'plugin_name', title: '插件', minWidth: 180, cell: 'plugin_name' },
                { colKey: 'module', title: '模块', width: 100 }, { colKey: 'db_version', title: '数据库版本', width: 120 },
                { colKey: 'disk_version', title: '文件版本', width: 120 }, { colKey: 'schema_status', title: '结构状态', width: 120, cell: 'schema_status' },
                { colKey: 'status', title: '运行状态', width: 130, cell: 'status' }, { colKey: 'last_scan_at', title: '最近扫描', width: 170 }, { colKey: 'op', title: '操作', width: 250, cell: 'op' },
            ];
            const statusOptions = ['current', 'version_mismatch', 'local_newer', 'schema_mismatch', 'missing_files', 'not_installed'].map(value => ({ label: value, value }));
            const stats = computed(() => [
                { key: 'total', label: '插件总数', value: total.value }, { key: 'version', label: '待升级', value: summary.value.version_mismatch || 0 },
                { key: 'schema', label: '结构异常', value: summary.value.schema_mismatch || 0 }, { key: 'files', label: '文件异常', value: summary.value.missing_files || 0 },
                { key: 'uninstalled', label: '未安装/未纳管', value: (summary.value.not_installed || 0) + (summary.value.unverified || 0) },
            ]);
            const warning = computed(() => { const bad = stats.value.slice(1).reduce((sum, item) => sum + Number(item.value || 0), 0); return bad ? `检测到 ${bad} 项异常，建议先刷新扫描，再通过本地控制中心执行停机计划。` : ''; });
            const load = () => { loading.value = true; request.get('/plugin-database/status', { params: { page: page.value, limit, keyword: keyword.value, module: module.value, status: status.value } }).then(res => { const data = res.data.data || res.data || {}; rows.value = data.list || []; total.value = data.total || 0; summary.value = data.summary || {}; }).finally(() => { loading.value = false; }); };
            const scan = () => { scanning.value = true; request.post('/plugin-database/scan').then(() => MessagePlugin.success('扫描任务已提交')).finally(() => { scanning.value = false; }); };
            const showDetail = row => { detailVisible.value = true; detailReport.value = JSON.stringify({ ...row, report: row.report || {} }, null, 2); };
            const prepareRepair = row => request.post(`/plugin-database/${row.plugin_name}/repair/prepare`).then(() => MessagePlugin.success('修复计划已生成')).catch(() => {});
            const prepareUpgrade = row => request.post('/plugin-database/prepare-batch', { names: [row.plugin_name] }).then(() => MessagePlugin.success('升级计划已生成')).catch(() => {});
            const openControlCenter = row => { window.open(`http://127.0.0.1:8765/?operation_id=${encodeURIComponent(row.pending_operation_id || '')}`, '_blank', 'noopener'); };
            const loadConfig = () => { request.get('/plugin-database/config').then(res => Object.assign(config, res.data.data || res.data || {})); configVisible.value = true; };
            const saveConfig = () => request.put('/plugin-database/config', config).then(() => { MessagePlugin.success('扫描配置已保存'); configVisible.value = false; }).catch(() => {});
            onMounted(load);
            return { rows, loading, scanning, total, page, limit, keyword, module, status, columns, statusOptions, stats, warning, detailVisible, detailReport, configVisible, config, load, scan, showDetail, prepareRepair, prepareUpgrade, openControlCenter, loadConfig, saveConfig };
        }
    });
})();
