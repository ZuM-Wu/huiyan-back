/**
 * 农户绑定页面脚本 — 对应路由 /admin/area-binding
 * 以产区为主：列出所有产区，行内多选下拉绑定多个农户（产区↔农户多对多）。
 * 「保存绑定」整集提交当前选中集合，「清空」解绑该产区全部农户。
 * 统一写法：Composition API + ES6 + HuiYan.createPage（baseURL=/api/admin/v1）
 */
(function () {
    'use strict';

    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            /* ========== 农户下拉选项（全量加载，无分页 limit） ========== */
            const farmerOptions = ref([]);
            const fetchFarmerOptions = () => {
                request.get('/farmer/options').then((res) => {
                    farmerOptions.value = res.data.data || [];
                }).catch(() => {});
            };
            // 依农户 id 渲染名称标签
            const farmerName = (fid) => {
                const f = farmerOptions.value.find((x) => x.id === fid);
                if (!f) return '#' + fid;
                return f.nickname ? (f.name + '（' + f.nickname + '）') : f.name;
            };
            // 已绑定标签的可见数量上限（卡片绑定区高度固定，超出以 +N 汇总）
            const BOUND_VISIBLE = 2;
            // 当前行可见的已绑定农户 id（最多 BOUND_VISIBLE 个）
            const visibleBound = (id) => (bindMap[id] || []).slice(0, BOUND_VISIBLE);
            // 当前行超出可见上限的已绑定农户数量（用于 +N 汇总标签）
            const extraBound = (id) => Math.max(0, (bindMap[id] || []).length - BOUND_VISIBLE);

            /* ========== 产区列表 ========== */
            const areaList = ref([]);
            const areaLoading = ref(false);
            const searchKeyword = ref('');
            const areaPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            // 每行当前选择的农户 id 集合（key=产区id），来源 /production-area/bindings
            const bindMap = reactive({});
            // 全部产区的绑定映射 {area_id: [farmer_id,...]}，用于列表初始化与刷新
            const bindingSource = ref({});
            // 产区地理数据映射 {area_id: {boundary, plots, center}}，来源 /{id}/geo（供只读缩略地图渲染）
            const geoMap = reactive({});

            /* ========== 地图配置（缩略地图需高德 JS API Key） ========== */
            const mapKey = ref('');
            const mapSecurity = ref('');
            const mapReady = ref(false);
            const fetchMapConfig = () => request.get('/production-area/map-config').then((res) => {
                const data = res.data.data || res.data;
                mapKey.value = data.amap_web_key || '';
                mapSecurity.value = data.amap_js_security_code || '';
                mapReady.value = data.production_area_map_enabled === '1' && !!mapKey.value;
            }).catch(() => {});

            // 清空全部地理数据（翻页前调用，触发缩略地图组件卸载重建）
            const clearGeos = () => {
                Object.keys(geoMap).forEach((id) => { delete geoMap[id]; });
            };

            // 逐个产区拉取地理数据（边界 + 中心 + 地块）；失败静默跳过，前端显示占位
            const fetchGeos = () => {
                areaList.value.forEach((a) => {
                    if (geoMap[a.id]) { return; }  // 已有则不重复请求
                    request.get('/production-area/' + a.id + '/geo').then((res) => {
                        const d = res.data.data || {};
                        geoMap[a.id] = {
                            boundary: d.boundary || '',
                            plots: d.plots || [],
                            center: (d.longitude && d.latitude) ? [d.longitude, d.latitude] : [],
                        };
                    }).catch(() => {});
                });
            };

            // 拉取全部产区的绑定映射（一次性），用于初始化每行多选
            const fetchBindings = () => request.get('/production-area/bindings').then((res) => {
                bindingSource.value = res.data.data || {};
            }).catch(() => { bindingSource.value = {}; });

            const applyBindMap = () => {
                areaList.value.forEach((a) => {
                    // 复制一份数组，避免与 bindingSource 共享引用
                    bindMap[a.id] = (bindingSource.value[a.id] || []).slice();
                });
            };

            const fetchAreas = () => {
                areaLoading.value = true;
                request.get('/production-area/list', {
                    params: {
                        page: areaPagination.current,
                        limit: areaPagination.pageSize,
                        keywords: searchKeyword.value,
                    }
                }).then((res) => {
                    // 后端已迁移统一响应信封 {status,msg,data}，列表载荷位于 data 层
                    areaList.value = (res.data.data || {}).list || [];
                    areaPagination.total = (res.data.data || {}).total || 0;
                    applyBindMap();
                    fetchGeos();
                }).catch(() => {}).finally(() => { areaLoading.value = false; });
            };

            const onAreaPageChange = (pageInfo) => {
                areaPagination.current = pageInfo.current;
                areaPagination.pageSize = pageInfo.pageSize;
                clearGeos();  // 翻页前清空旧地理数据
                fetchAreas();
            };

            /* ========== 绑定 / 解绑（整集替换） ========== */
            const doBind = (row, farmerIds) => {
                request.put('/production-area/' + row.id + '/farmers', { farmer_ids: farmerIds }).then((res) => {
                    MessagePlugin.success(res.data.msg || '操作成功');
                    // 同步本地绑定源并回填当前行
                    bindingSource.value[row.id] = farmerIds.slice();
                }).catch(() => {});
            };

            const saveBind = (row) => {
                doBind(row, (bindMap[row.id] || []).map(Number));
            };

            const unbind = (row) => {
                bindMap[row.id] = [];
                doBind(row, []);
            };

            onMounted(() => {
                fetchMapConfig();
                fetchFarmerOptions();
                // 先拉绑定映射再拉列表，保证初始化多选正确
                fetchBindings().then(fetchAreas);
            });

            return {
                farmerOptions, farmerName, visibleBound, extraBound,
                areaList, areaLoading, searchKeyword, areaPagination, bindMap,
                geoMap, mapKey, mapSecurity, mapReady,
                fetchAreas, onAreaPageChange, saveBind, unbind,
            };
        }
    });
})();
