/**
 * 产区详情页面脚本 — 对应路由 /admin/area-detail?area_id=x
 * 左侧整体地图（amap-board）叠加产区参考边界 + 已有地块（不同颜色 + 名称标注），
 * 支持在图上多次框选新增地块；右侧地块列表支持编辑/停用/删除（复用 /plot 接口与 plot:* 权限）。
 * 统一写法：Composition API + ES6 + HuiYan.createPage（baseURL=/api/admin/v1）
 */
(function () {
    'use strict';

    const { ref, reactive, computed, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            const qs = new URLSearchParams(window.location.search);
            const areaId = qs.get('area_id') ? Number(qs.get('area_id')) : 0;

            /* ========== 产区信息 ========== */
            const area = reactive({
                id: 0, name: '', code: '', boundary: '', longitude: 0, latitude: 0,
                crop_category: '', area_size: 0, province: '', city: '', district: '',
                address: '', status: 1, description: '', farmer_count: 0,
            });
            const center = computed(() => (area.longitude && area.latitude)
                ? [area.longitude, area.latitude] : []);
            // 地区拼接：省 市 区/县（空段自动跳过）
            const regionText = computed(() =>
                [area.province, area.city, area.district].filter(Boolean).join(' ') || '—');

            const fetchArea = () => request.get('/production-area/' + areaId).then((res) => {
                Object.assign(area, res.data.data || res.data || {});
            }).catch(() => { MessagePlugin.error('获取产区详情失败'); });

            /* ========== 地块列表 ========== */
            const plotList = ref([]);
            const plotLoading = ref(false);
            const plotColumns = [
                { colKey: 'name', title: '地块名称', ellipsis: true },
                { colKey: 'code', title: '编号', width: 110, ellipsis: true },
                { colKey: 'soil_type', title: '土壤类型', width: 110, ellipsis: true },
                { colKey: 'area_size', title: '面积(亩)', width: 90 },
                { colKey: 'status', title: '状态', width: 80, cell: 'status' },
                { colKey: 'operation', title: '操作', width: 240, cell: 'operation', fixed: 'right' },
            ];

            const fetchPlots = () => {
                plotLoading.value = true;
                request.get('/plot/list', { params: { area_id: areaId, limit: 100 } }).then((res) => {
                    plotList.value = res.data.data.list || [];
                }).catch(() => {}).finally(() => { plotLoading.value = false; });
            };

            /* ========== 地块表单（框选新增 / 编辑） ========== */
            const dialogVisible = ref(false);
            const dialogTitle = ref('新增地块');
            const saving = ref(false);
            const defaultForm = () => ({
                id: 0, name: '', code: '', soil_type: '',
                longitude: 0, latitude: 0, boundary: '',
                area_size: 0, sort_order: 0, description: '',
            });
            const form = reactive(defaultForm());
            const resetForm = (data) => { Object.assign(form, defaultForm(), data || {}); };

            // 地图框选完成回调：带入边界/中心点/面积，弹表单补充信息
            const onPlotDrawn = (payload) => {
                resetForm({
                    boundary: payload.boundary || '',
                    longitude: payload.longitude || 0,
                    latitude: payload.latitude || 0,
                    area_size: payload.area_size || 0,
                });
                dialogTitle.value = '新增地块';
                dialogVisible.value = true;
                // 清除上一次未通过的校验提示
                if (formRef.value) formRef.value.clearValidate();
            };

            /* ========== 地图看板引用（供「补画边界」调用绘制方法） ========== */
            const boardRef = ref(null);
            // 列表「补画边界」：为无边界地块在上方地图拉框绘制边界，完成后经 onPlotUpdated 全量回填
            const drawBoundaryFor = (row) => {
                if (!mapReady.value || !boardRef.value) {
                    MessagePlugin.warning('地图未开启，无法补画边界');
                    return;
                }
                boardRef.value.startDrawForPlot(row.id);
                MessagePlugin.info('请在上方地图为地块「' + row.name + '」拉框绘制边界');
            };

            const openEdit = (row) => {
                request.get('/plot/' + row.id).then((res) => {
                    resetForm(res.data.data || res.data);
                    dialogTitle.value = '编辑地块';
                    dialogVisible.value = true;
                    if (formRef.value) formRef.value.clearValidate();
                }).catch(() => { MessagePlugin.error('获取地块详情失败'); });
            };

            // 地图上拖动顶点修改已有地块边界后：合并新边界/面积，整体提交
            // （/plot PUT 为全量更新，必须带齐原有字段避免被清空）
            const onPlotUpdated = (payload) => {
                const row = plotList.value.find((p) => p.id === payload.id);
                if (!row) { return; }
                const body = {
                    name: row.name, code: row.code, soil_type: row.soil_type,
                    longitude: Number(row.longitude) || 0,
                    latitude: Number(row.latitude) || 0,
                    boundary: payload.boundary || '',
                    area_size: Number(payload.area_size) || 0,
                    sort_order: Number(row.sort_order) || 0,
                    description: row.description || '',
                };
                request.put('/plot/' + payload.id, body).then((res) => {
                    MessagePlugin.success(res.data.msg || '地块边界已更新');
                    fetchPlots();
                }).catch(() => {});
            };

            // 表单内联校验（视觉规范 9.9）：必填提示显示在输入框下方，禁止 MessagePlugin 弹出
            const formRef = ref(null);
            const rules = {
                name: [{ required: true, message: '请输入地块名称', type: 'error' }],
            };

            const doSave = () => {
                if (!formRef.value) return;
                // 内联校验：未通过时错误已显示在对应输入框下方，直接中断
                formRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    saving.value = true;
                    const payload = {
                        name: form.name, code: form.code, soil_type: form.soil_type,
                        longitude: Number(form.longitude) || 0,
                        latitude: Number(form.latitude) || 0,
                        boundary: form.boundary || '',
                        area_size: Number(form.area_size) || 0,
                        sort_order: Number(form.sort_order) || 0,
                        description: form.description,
                    };
                    let p;
                    if (form.id) {
                        p = request.put('/plot/' + form.id, payload);
                    } else {
                        payload.area_id = areaId;
                        p = request.post('/plot', payload);
                    }
                    p.then((res) => {
                        MessagePlugin.success(res.data.msg || '保存成功');
                        dialogVisible.value = false;
                        fetchPlots();
                    }).catch(() => {}).finally(() => { saving.value = false; });
                });
            };

            const toggleStatus = (row) => {
                const next = row.status === 1 ? 0 : 1;
                request.put('/plot/' + row.id + '/status', { status: next }).then((res) => {
                    MessagePlugin.success(res.data.msg || '操作成功');
                    fetchPlots();
                }).catch(() => {});
            };

            const removePlot = (row) => {
                const instance = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '确定删除地块「' + row.name + '」吗？若下有批次请先处理。',
                    theme: 'danger',
                    onConfirm: () => {
                        request.delete('/plot/' + row.id).then((res) => {
                            MessagePlugin.success(res.data.msg || '已删除');
                            instance.destroy();
                            fetchPlots();
                        }).catch(() => { instance.destroy(); });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            const goBack = () => { window.location.href = '/admin/production-area'; };

            /* ========== 地图配置 ========== */
            const mapKey = ref('');
            const mapSecurity = ref('');
            const mapReady = ref(false);
            const fetchMapConfig = () => request.get('/production-area/map-config').then((res) => {
                const data = res.data.data || res.data;
                mapKey.value = data.amap_web_key || '';
                mapSecurity.value = data.amap_js_security_code || '';
                mapReady.value = data.production_area_map_enabled === '1' && !!mapKey.value;
            }).catch(() => {});

            onMounted(() => {
                if (!areaId) { MessagePlugin.error('缺少产区参数'); return; }
                // 先拿地图配置与产区信息，再拉地块；地图组件在 mapReady 后挂载
                fetchMapConfig();
                fetchArea();
                fetchPlots();
            });

            return {
                area, center, regionText, plotList, plotLoading, plotColumns,
                dialogVisible, dialogTitle, saving, form, formRef, rules,
                onPlotDrawn, onPlotUpdated, openEdit, doSave, toggleStatus, removePlot, goBack,
                boardRef, drawBoundaryFor,
                mapKey, mapSecurity, mapReady,
            };
        }
    });
})();
