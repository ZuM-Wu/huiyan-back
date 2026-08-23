/**
 * 产区管理页面脚本 — 对应路由 /admin/production-area
 * 两个 Tab：产区列表（CRUD + 编辑弹窗内嵌 地图框选/手动填写）、地图设置
 * 统一写法：Composition API + ES6 + HuiYan.createPage（baseURL=/api/admin/v1）
 */
(function () {
    'use strict';

    const { ref, reactive, watch, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('list', ['list', 'map']));
            watch(activeTab, (val) => { HuiYan.syncUrlTab(val); });

            /* ========== 产区列表 ========== */
            const areaList = ref([]);
            const areaLoading = ref(false);
            const searchKeyword = ref('');
            const areaPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const areaColumns = [
                { colKey: 'id', title: 'ID', width: 70 },
                { colKey: 'name', title: '产区名称', width: 160, ellipsis: true },
                { colKey: 'code', title: '编号', width: 120, ellipsis: true },
                { colKey: 'farmer_count', title: '归属农户', width: 110, cell: 'farmer_count' },
                { colKey: 'crop_category', title: '主要作物', width: 120, ellipsis: true },
                { colKey: 'area_size', title: '面积(亩)', width: 100 },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'operation', title: '操作', width: 240, cell: 'operation', fixed: 'right' },
            ];

            const fetchAreas = () => {
                areaLoading.value = true;
                request.get('/production-area/list', {
                    params: {
                        page: areaPagination.current,
                        limit: areaPagination.pageSize,
                        keywords: searchKeyword.value,
                    }
                }).then((res) => {
                    areaList.value = res.data.data.list || [];
                    areaPagination.total = res.data.data.total || 0;
                }).catch(() => {}).finally(() => {
                    areaLoading.value = false;
                });
            };

            const onAreaPageChange = (pageInfo) => {
                areaPagination.current = pageInfo.current;
                areaPagination.pageSize = pageInfo.pageSize;
                fetchAreas();
            };

            /* ========== 编辑弹窗 ========== */
            const dialogVisible = ref(false);
            const dialogTitle = ref('新增产区');
            const saving = ref(false);
            const geoTab = ref('manual');
            const defaultForm = () => ({
                id: 0, name: '', code: '', crop_category: '',
                province: '', city: '', district: '', address: '',
                longitude: 0, latitude: 0, boundary: '',
                area_size: 0, sort_order: 0, description: '',
            });
            const form = reactive(defaultForm());

            // 地图组件 v-model（{longitude, latitude, boundary, 省/市/区/地址}）与手动填写 Tab 双向同步
            const geoModel = reactive({ longitude: 0, latitude: 0, boundary: '', province: '', city: '', district: '', address: '' });
            // 每次打开弹窗递增，强制 amap-picker 重新挂载以从当前值重新初始化（修复再次进入地图丢失回显）
            const geoKey = ref(0);
            // 地图组件回传：就地合并到 geoModel（reactive 不能直接重赋值，否则 v-model 无法回写）
            const onGeoUpdate = (val) => { Object.assign(geoModel, val || {}); };
            // 地图 → 表单
            watch(geoModel, (val) => {
                if (val.longitude !== undefined) form.longitude = val.longitude;
                if (val.latitude !== undefined) form.latitude = val.latitude;
                if (val.boundary !== undefined) form.boundary = val.boundary;
                // 地图框选边界后自动回填总面积（与地块逻辑一致）
                if (val.area_size) form.area_size = val.area_size;
                if (val.province) form.province = val.province;
                if (val.city) form.city = val.city;
                if (val.district) form.district = val.district;
                if (val.address) form.address = val.address;
            }, { deep: true });
            // 手动填写 → 地图（经纬度/边界）
            watch(() => [form.longitude, form.latitude, form.boundary], (arr) => {
                geoModel.longitude = Number(arr[0]) || 0;
                geoModel.latitude = Number(arr[1]) || 0;
                geoModel.boundary = arr[2] || '';
            });

            // 弹窗弹出动画未结束时（scale 从 0.01 过渡到 1），内嵌 Tabs 的激活指示条会按动画初始尺寸（近 0）
            // 计算宽度而不显示；地图也因容器尺寸不准而不渲染。此 key 在弹窗动画结束后递增，
            // 强制内嵌 Tabs（含地图选址组件）在正确尺寸下重新挂载，一次性修复指示条与地图渲染。
            const geoTabsKey = ref(0);
            const onDialogOpened = () => { geoTabsKey.value++; };

            // 表单内联校验（视觉规范 9.9）：必填提示显示在输入框下方，禁止 MessagePlugin 弹出
            const formRef = ref(null);
            const rules = {
                name: [{ required: true, message: '请输入产区名称', type: 'error' }],
            };

            const resetForm = (data) => {
                Object.assign(form, defaultForm(), data || {});
                geoModel.longitude = form.longitude || 0;
                geoModel.latitude = form.latitude || 0;
                geoModel.boundary = form.boundary || '';
                geoModel.province = form.province || '';
                geoModel.city = form.city || '';
                geoModel.district = form.district || '';
                geoModel.address = form.address || '';
            };

            const openCreate = () => {
                resetForm();
                dialogTitle.value = '新增产区';
                geoTab.value = mapEnabled.value ? 'map' : 'manual';
                geoKey.value++;
                dialogVisible.value = true;
                // 清除上一次未通过的校验提示
                if (formRef.value) formRef.value.clearValidate();
            };

            const openEdit = (row) => {
                request.get('/production-area/' + row.id).then((res) => {
                    resetForm(res.data.data || res.data);
                    dialogTitle.value = '编辑产区';
                    geoTab.value = mapEnabled.value ? 'map' : 'manual';
                    geoKey.value++;
                    dialogVisible.value = true;
                    if (formRef.value) formRef.value.clearValidate();
                }).catch(() => { MessagePlugin.error('获取产区详情失败'); });
            };

            const doSave = () => {
                if (!formRef.value) return;
                // 内联校验：未通过时错误已显示在对应输入框下方，直接中断
                formRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    saving.value = true;
                    const payload = {
                        name: form.name, code: form.code, crop_category: form.crop_category,
                        province: form.province, city: form.city, district: form.district,
                        address: form.address,
                        longitude: Number(form.longitude) || 0,
                        latitude: Number(form.latitude) || 0,
                        boundary: form.boundary || '',
                        area_size: Number(form.area_size) || 0,
                        sort_order: Number(form.sort_order) || 0,
                        description: form.description,
                    };
                    const p = form.id
                        ? request.put('/production-area/' + form.id, payload)
                        : request.post('/production-area', payload);
                    p.then((res) => {
                        MessagePlugin.success(res.data.msg || '保存成功');
                        dialogVisible.value = false;
                        fetchAreas();
                    }).catch(() => {}).finally(() => { saving.value = false; });
                });
            };

            const toggleStatus = (row) => {
                const next = row.status === 1 ? 0 : 1;
                request.put('/production-area/' + row.id + '/status', { status: next }).then((res) => {
                    MessagePlugin.success(res.data.msg || '操作成功');
                    fetchAreas();
                }).catch(() => {});
            };

            const removeArea = (row) => {
                const instance = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '确定删除产区「' + row.name + '」吗？若下有地块请先处理。',
                    theme: 'danger',
                    onConfirm: () => {
                        request.delete('/production-area/' + row.id).then((res) => {
                            MessagePlugin.success(res.data.msg || '已删除');
                            instance.destroy();
                            fetchAreas();
                        }).catch(() => { instance.destroy(); });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            const goPlots = (row) => {
                window.location.href = '/admin/area-detail?area_id=' + row.id;
            };

            /* ========== 地图设置 ========== */
            const mapForm = reactive({ amap_web_key: '', amap_js_security_code: '', amap_web_service_key: '', production_area_map_enabled: '1' });
            const mapSaving = ref(false);
            const mapKey = ref('');
            const mapSecurity = ref('');
            const mapEnabled = ref(false);

            const fetchMapConfig = () => {
                return request.get('/production-area/map-config').then((res) => {
                    const data = res.data.data || res.data;
                    Object.assign(mapForm, data);
                    mapKey.value = data.amap_web_key || '';
                    mapSecurity.value = data.amap_js_security_code || '';
                    mapEnabled.value = data.production_area_map_enabled === '1' && !!mapKey.value;
                }).catch(() => { /* 无地图配置权限时静默降级为手动填写 */ });
            };

            const saveMapConfig = () => {
                mapSaving.value = true;
                request.put('/production-area/map-config', mapForm).then((res) => {
                    MessagePlugin.success(res.data.msg || '地图配置已保存');
                    fetchMapConfig();
                }).catch(() => {}).finally(() => { mapSaving.value = false; });
            };

            onMounted(() => {
                fetchAreas();
                fetchMapConfig();
            });

            return {
                activeTab,
                areaList, areaLoading, searchKeyword, areaPagination, areaColumns,
                fetchAreas, onAreaPageChange,
                dialogVisible, dialogTitle, saving, form, formRef, rules, geoTab, geoModel, geoKey, geoTabsKey, onGeoUpdate, onDialogOpened,
                openCreate, openEdit, doSave, toggleStatus, removeArea, goPlots,
                mapForm, mapSaving, saveMapConfig,
                mapKey, mapSecurity, mapEnabled,
            };
        }
    });
})();
