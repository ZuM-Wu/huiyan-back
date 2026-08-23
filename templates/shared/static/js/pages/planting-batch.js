/**
 * 种植批次页面脚本 — 对应路由 /admin/planting-batch
 * 批次列表（带产区/地块筛选）+ 编辑弹窗（纯表单，无地图）
 * 支持 URL ?plot_id=x 预筛选（由地块列表「管理批次」跳转带入）
 * 统一写法：Composition API + ES6 + HuiYan.createPage（baseURL=/api/admin/v1）
 */
(function () {
    'use strict';

    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    // 批次状态映射
    const STATUS_MAP = { 0: '未开始', 1: '种植中', 2: '已采收', 3: '异常' };
    const STATUS_THEME = { 0: 'default', 1: 'success', 2: 'primary', 3: 'danger' };

    HuiYan.createPage({
        setup() {
            const qs = new URLSearchParams(window.location.search);
            const initPlotId = qs.get('plot_id') ? Number(qs.get('plot_id')) : null;

            const statusLabel = (s) => STATUS_MAP[s] || '未知';
            const statusTheme = (s) => STATUS_THEME[s] || 'default';
            const statusOptions = [
                { content: '未开始', value: 0 },
                { content: '种植中', value: 1 },
                { content: '已采收', value: 2 },
                { content: '异常', value: 3 },
            ];

            /* ========== 列表 ========== */
            const batchList = ref([]);
            const batchLoading = ref(false);
            const searchKeyword = ref('');
            const filterAreaId = ref(null);
            const filterPlotId = ref(initPlotId);
            const areaOptions = ref([]);
            const plotOptions = ref([]);
            const batchPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const batchColumns = [
                { colKey: 'id', title: 'ID', width: 70 },
                { colKey: 'batch_no', title: '批次编号', width: 130, ellipsis: true },
                { colKey: 'crop_name', title: '作物', width: 110, ellipsis: true },
                { colKey: 'crop_variety', title: '品种', width: 110, ellipsis: true },
                { colKey: 'plot_name', title: '所属地块', width: 140, ellipsis: true },
                { colKey: 'season', title: '茬口', width: 90, ellipsis: true },
                { colKey: 'plant_date', title: '种植日期', width: 120 },
                { colKey: 'plant_count', title: '株数', width: 90 },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'operation', title: '操作', width: 200, cell: 'operation', fixed: 'right' },
            ];

            const fetchAreaOptions = () => {
                request.get('/production-area/options').then((res) => {
                    areaOptions.value = res.data.data || [];
                }).catch(() => {});
            };

            const fetchPlotOptions = (areaId) => {
                const params = {};
                if (areaId) params.area_id = areaId;
                request.get('/plot/options', { params: params }).then((res) => {
                    plotOptions.value = res.data.data || [];
                }).catch(() => {});
            };

            const onAreaFilterChange = () => {
                filterPlotId.value = null;
                fetchPlotOptions(filterAreaId.value);
                fetchBatches();
            };

            const fetchBatches = () => {
                batchLoading.value = true;
                const params = {
                    page: batchPagination.current,
                    limit: batchPagination.pageSize,
                    keywords: searchKeyword.value,
                };
                if (filterAreaId.value) params.area_id = filterAreaId.value;
                if (filterPlotId.value) params.plot_id = filterPlotId.value;
                request.get('/planting-batch/list', { params: params }).then((res) => {
                    batchList.value = res.data.data.list || [];
                    batchPagination.total = res.data.data.total || 0;
                }).catch(() => {}).finally(() => { batchLoading.value = false; });
            };

            const onBatchPageChange = (pageInfo) => {
                batchPagination.current = pageInfo.current;
                batchPagination.pageSize = pageInfo.pageSize;
                fetchBatches();
            };

            /* ========== 编辑弹窗 ========== */
            const dialogVisible = ref(false);
            const dialogTitle = ref('新增批次');
            const saving = ref(false);
            // 表单内联校验（视觉规范 9.9）：必填提示显示在输入框下方，禁止 MessagePlugin 弹出
            const formRef = ref(null);
            const rules = {
                plot_id: [{ required: true, message: '请选择所属地块', type: 'error' }],
                crop_name: [{ required: true, message: '请输入作物名称', type: 'error' }],
            };
            // 弹窗内级联：先选产区再选地块（独立于列表筛选的 plotOptions）
            const dialogAreaId = ref(null);
            const dialogPlotOptions = ref([]);

            const fetchDialogPlotOptions = (areaId) => {
                if (!areaId) { dialogPlotOptions.value = []; return; }
                request.get('/plot/options', { params: { area_id: areaId } }).then((res) => {
                    dialogPlotOptions.value = res.data.data || [];
                }).catch(() => { dialogPlotOptions.value = []; });
            };

            const onDialogAreaChange = () => {
                form.plot_id = null;
                fetchDialogPlotOptions(dialogAreaId.value);
            };
            const defaultForm = () => ({
                id: 0, plot_id: null, batch_no: '', crop_name: '', crop_variety: '',
                season: '', plant_date: '', expected_harvest_date: '', actual_harvest_date: '',
                plant_count: 0, description: '',
            });
            const form = reactive(defaultForm());

            const openCreate = () => {
                Object.assign(form, defaultForm(), { plot_id: filterPlotId.value || null });
                // 预填弹窗产区（使用列表筛选的产区），并加载对应地块
                dialogAreaId.value = filterAreaId.value || null;
                if (dialogAreaId.value) {
                    fetchDialogPlotOptions(dialogAreaId.value);
                } else {
                    dialogPlotOptions.value = [];
                }
                dialogTitle.value = '新增批次';
                dialogVisible.value = true;
                // 清除上一次未通过的校验提示
                if (formRef.value) formRef.value.clearValidate();
            };

            const openEdit = (row) => {
                request.get('/planting-batch/' + row.id).then((res) => {
                    Object.assign(form, defaultForm(), res.data.data || res.data);
                    dialogTitle.value = '编辑批次';
                    dialogVisible.value = true;
                    if (formRef.value) formRef.value.clearValidate();
                }).catch(() => { MessagePlugin.error('获取批次详情失败'); });
            };

            const doSave = () => {
                if (!formRef.value) return;
                // 内联校验：未通过时错误已显示在对应输入框下方，直接中断
                formRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    saving.value = true;
                    const payload = {
                        batch_no: form.batch_no, crop_name: form.crop_name,
                        crop_variety: form.crop_variety, season: form.season,
                        plant_date: form.plant_date || '',
                        expected_harvest_date: form.expected_harvest_date || '',
                        actual_harvest_date: form.actual_harvest_date || '',
                        plant_count: Number(form.plant_count) || 0,
                        description: form.description,
                    };
                    let p;
                    if (form.id) {
                        p = request.put('/planting-batch/' + form.id, payload);
                    } else {
                        payload.plot_id = Number(form.plot_id);
                        p = request.post('/planting-batch', payload);
                    }
                    p.then((res) => {
                        MessagePlugin.success(res.data.msg || '保存成功');
                        dialogVisible.value = false;
                        fetchBatches();
                    }).catch(() => {}).finally(() => { saving.value = false; });
                });
            };

            const changeStatus = (row, status) => {
                request.put('/planting-batch/' + row.id + '/status', { status: status }).then((res) => {
                    MessagePlugin.success(res.data.msg || '状态已更新');
                    fetchBatches();
                }).catch(() => {});
            };

            const removeBatch = (row) => {
                const instance = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '确定删除批次「' + (row.batch_no || row.crop_name) + '」吗？',
                    theme: 'danger',
                    onConfirm: () => {
                        request.delete('/planting-batch/' + row.id).then((res) => {
                            MessagePlugin.success(res.data.msg || '已删除');
                            instance.destroy();
                            fetchBatches();
                        }).catch(() => { instance.destroy(); });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            onMounted(() => {
                fetchAreaOptions();
                fetchPlotOptions(null);
                fetchBatches();
            });

            return {
                batchList, batchLoading, searchKeyword,
                filterAreaId, filterPlotId, areaOptions, plotOptions,
                batchPagination, batchColumns, fetchBatches, onBatchPageChange,
                onAreaFilterChange,
                dialogVisible, dialogTitle, saving, form, formRef, rules,
                dialogAreaId, dialogPlotOptions, onDialogAreaChange,
                openCreate, openEdit, doSave, changeStatus, removeBatch,
                statusLabel, statusTheme, statusOptions,
            };
        }
    });
})();
