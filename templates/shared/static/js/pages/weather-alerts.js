/**
 * 天气服务页「预警记录」Tab 逻辑（从 weather.js 拆出，控制单文件行数）
 *
 * 以工厂函数形式返回该 Tab 的响应式状态与方法，供 weather.js 的 setup 合并进页面。
 * - 全量天气预警分页列表（产区/等级/生效状态/日期范围筛选 + 服务端分页 + 详情弹窗）
 * - 产区下拉数据源由 weather.js 复用 Tab3 的 bindingList（本文件不重复请求）
 * - 懒加载：首次进入预警 Tab（或磁贴 ?tab=alerts 直达）才拉取数据
 *
 * 依赖全局：Vue（CDN）、request（页面请求实例，自带 JWT 头与 /api/admin/v1 前缀）
 *
 * 挂载：页面协作全局 window.WeatherAlertsTab（不进 HuiYan 公共命名空间）
 */
(function () {
    /**
     * 创建预警记录 Tab 的状态与方法
     * @param {object} activeTab 主 Tab 的 ref（用于监听切换实现懒加载）
     * @returns {object} 供页面 setup return 合并的响应式状态与方法
     */
    window.WeatherAlertsTab = function (activeTab) {
        const { ref, reactive, watch } = Vue;

        const alertTableData = ref([]);
        const alertLoading = ref(false);
        const alertFilterAreaId = ref(null);
        const alertFilterLevel = ref(null);
        const alertFilterActive = ref(null);
        const alertDateRange = ref([]);
        const alertDetailVisible = ref(false);
        const alertDetailRow = ref(null);
        const alertPagination = reactive({ current: 1, pageSize: 20, total: 0 });

        const alertColumns = [
            { colKey: 'id', title: 'ID', width: 70 },
            { colKey: 'title', title: '预警标题', minWidth: 200, ellipsis: true },
            { colKey: 'alert_type', title: '类型', width: 90, ellipsis: true },
            { colKey: 'level', title: '等级', width: 80, cell: 'level' },
            { colKey: 'active', title: '状态', width: 90, cell: 'active' },
            { colKey: 'area_name', title: '产区', width: 120, ellipsis: true },
            { colKey: 'start_time', title: '生效时间', width: 170, ellipsis: true },
            { colKey: 'end_time', title: '结束时间', width: 170, ellipsis: true },
            { colKey: 'op', title: '操作', width: 80, cell: 'op' },
        ];

        // 等级归一化: 和风源以英文色值入库（yellow/orange 等），统一显示为中文
        const LEVEL_TEXT = { blue: '蓝色', yellow: '黄色', orange: '橙色', red: '红色' };
        const levelText = function (level) {
            if (!level) return '-';
            return LEVEL_TEXT[String(level).toLowerCase()] || level;
        };

        // 等级 -> t-tag 主题映射（蓝色 primary / 黄色、橙色 warning / 红色 danger）
        const levelTheme = function (level) {
            const text = levelText(level);
            if (text === '红色') return 'danger';
            if (text === '橙色' || text === '黄色') return 'warning';
            if (text === '蓝色') return 'primary';
            return 'default';
        };

        function fetchAlerts() {
            alertLoading.value = true;
            const params = {
                page: alertPagination.current,
                limit: alertPagination.pageSize,
            };
            if (alertFilterAreaId.value) params.area_id = alertFilterAreaId.value;
            if (alertFilterLevel.value) params.level = alertFilterLevel.value;
            if (alertFilterActive.value !== null && alertFilterActive.value !== '') {
                params.active = alertFilterActive.value;
            }
            if (alertDateRange.value && alertDateRange.value.length === 2) {
                params.start_date = alertDateRange.value[0];
                params.end_date = alertDateRange.value[1];
            }
            request.get('/weather/alerts/list', { params: params })
                .then(function (res) {
                    const data = res.data.data || res.data;
                    alertTableData.value = data.list || [];
                    alertPagination.total = data.total || 0;
                })
                .catch(function () { })
                .finally(function () { alertLoading.value = false; });
        }

        function onAlertPageChange(pageInfo) {
            alertPagination.current = pageInfo.current;
            alertPagination.pageSize = pageInfo.pageSize;
            fetchAlerts();
        }

        function showAlertDetail(row) {
            alertDetailRow.value = row;
            alertDetailVisible.value = true;
        }

        // 懒加载：首次进入预警 Tab 才拉取；后续切换不重复请求
        let loadedOnce = false;
        function maybeLoad() {
            if (loadedOnce) return;
            loadedOnce = true;
            fetchAlerts();
        }
        watch(activeTab, function (val) {
            if (val === 'alerts') maybeLoad();
        });
        // 磁贴 ?tab=alerts 直达时初始即为预警 Tab，watch 不触发初始值，立即补一次
        if (activeTab.value === 'alerts') maybeLoad();

        return {
            alertTableData, alertLoading, alertFilterAreaId, alertFilterLevel,
            alertFilterActive, alertDateRange, alertDetailVisible, alertDetailRow,
            alertPagination, alertColumns, levelText, levelTheme,
            fetchAlerts, onAlertPageChange, showAlertDetail,
        };
    };
})();
