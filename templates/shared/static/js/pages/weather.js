/**
 * 天气服务管理页 — 四Tab结构（数据源插件 + 拉取设置 + 产区绑定 + 数据查询）
 *
 * 对标 notice-sms.js 范式：
 * - Tab1: 天气数据源插件（安装/卸载/配置动态表单/测试/启停）
 * - Tab2: 全局拉取设置（总开关/默认源/频率/积温基点，保存即时生效）
 * - Tab3: 产区数据源绑定 + 最近拉取状态 + 手动刷新/全量拉取 + 天气详情查看
 * - Tab4: 数据查询（选产区+批次看活动/有效积温，产区积温走势图，逐日历史支持日期范围）
 */
(function () {
HuiYan.createPage({
    setup() {
        const { ref, reactive, onMounted, computed, watch, nextTick } = Vue;
        const { MessagePlugin, DialogPlugin } = TDesign;

        // 封装 DialogPlugin.confirm 为 Promise（TDesign 原生是回调式）
        function confirmAsync(options) {
            return new Promise(function (resolve) {
                var instance = DialogPlugin.confirm({
                    header: options.header,
                    body: options.body,
                    onConfirm: function () { instance.destroy(); resolve(true); },
                    onClose: function () { instance.destroy(); resolve(false); },
                    onCancel: function () { instance.destroy(); resolve(false); }
                });
            });
        }

        // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
        const activeTab = ref(HuiYan.getUrlTab('sources', ['sources', 'settings', 'bindings', 'query', 'alerts']));
        watch(activeTab, function (val) { HuiYan.syncUrlTab(val); });

        // ============================================================
        // Tab1: 数据源插件
        // ============================================================
        const sourceList = ref([]);
        const sourceLoading = ref(false);

        async function fetchSources() {
            sourceLoading.value = true;
            try {
                const res = await request.get('/weather/sources');
                sourceList.value = res.data.data.list || [];
            } finally {
                sourceLoading.value = false;
            }
        }

        const sourceColumns = [
            { colKey: 'title', title: '数据源名称', minWidth: 160 },
            { colKey: 'description', title: '说明', minWidth: 240, ellipsis: true },
            { colKey: 'version', title: '版本', width: 90 },
            { colKey: 'status', title: '状态', width: 110 },
            { colKey: 'operation', title: '操作', width: 260, fixed: 'right' },
        ];

        // 已启用的插件（设置页默认源与产区绑定下拉共用）
        const enabledSources = computed(function () {
            return sourceList.value.filter(function (p) { return p.installed && p.status === 1; });
        });

        async function installSource(row) {
            try {
                await request.post('/plugin/install/' + row.name);
                MessagePlugin.success('插件安装成功');
                fetchSources();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '安装失败');
            }
        }

        async function uninstallSource(row) {
            var ok = await confirmAsync({ header: '确认卸载', body: '确定卸载插件「' + row.title + '」吗？已落库的天气数据不会丢失。' });
            if (ok) {
                try {
                    await request.post('/plugin/uninstall/' + row.name);
                    MessagePlugin.success('已卸载');
                    fetchSources();
                } catch (e) {
                    MessagePlugin.error(e.response?.data?.detail || '卸载失败');
                }
            }
        }

        async function toggleSource(row, status) {
            try {
                await request.put('/weather/sources/' + row.name + '/status', { status: status });
                MessagePlugin.success(status === 1 ? '已启用' : '已禁用');
                fetchSources();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '操作失败');
            }
        }

        async function testSource(row) {
            MessagePlugin.info('正在测试连通性...');
            try {
                const res = await request.post('/weather/sources/' + row.name + '/test');
                var td = res.data.data || {};
                if (td.success) {
                    MessagePlugin.success(td.message || '连接成功');
                } else {
                    MessagePlugin.warning('连接失败: ' + (td.message || '未知错误'));
                }
            } catch (e) {
                MessagePlugin.error('测试失败');
            }
        }

        // 配置弹窗（动态表单，按 config_schema 渲染）
        const configVisible = ref(false);
        const configPluginName = ref('');
        const configPluginTitle = ref('');
        const configSchema = ref([]);
        const configForm = reactive({});
        const configSaving = ref(false);

        const configRules = computed(function () {
            var rules = {};
            configSchema.value.forEach(function (f) {
                if (f.required) {
                    rules[f.key] = [{ required: true, message: '请输入' + f.label, type: 'error' }];
                }
            });
            return rules;
        });

        function openConfig(row) {
            configPluginName.value = row.name;
            configPluginTitle.value = row.title;
            configSchema.value = row.config_schema || [];
            Object.keys(configForm).forEach(function (k) { delete configForm[k]; });
            (row.config_schema || []).forEach(function (f) {
                configForm[f.key] = (row.config || {})[f.key] !== undefined ? (row.config || {})[f.key] : (f.default || '');
            });
            configVisible.value = true;
        }

        function saveConfig(params) {
            if (params && params.e) params.e.preventDefault();
            if (params && params.validateResult !== true) return;
            doSaveConfig();
        }

        async function doSaveConfig() {
            configSaving.value = true;
            try {
                await request.post('/weather/sources/' + configPluginName.value + '/config', { config: { ...configForm } });
                MessagePlugin.success('配置已保存');
                configVisible.value = false;
                fetchSources();
            } finally {
                configSaving.value = false;
            }
        }

        // ============================================================
        // Tab2: 拉取设置
        // ============================================================
        const settingsForm = reactive({
            enabled: false, source: '', interval_minutes: 15, gdd_base_temp: 10,
            daily_retention_days: 730, alert_retention_days: 90,
        });
        const settingsSaving = ref(false);

        async function fetchSettings() {
            try {
                const res = await request.get('/weather/settings');
                var d = res.data.data || {};
                settingsForm.enabled = !!d.enabled;
                settingsForm.source = d.source || '';
                settingsForm.interval_minutes = d.interval_minutes || 15;
                settingsForm.gdd_base_temp = d.gdd_base_temp !== undefined ? d.gdd_base_temp : 10;
                settingsForm.daily_retention_days = d.daily_retention_days || 730;
                settingsForm.alert_retention_days = d.alert_retention_days || 90;
            } catch (e) { /* 拦截器已提示 */ }
        }

        function saveSettings(params) {
            if (params && params.e) params.e.preventDefault();
            doSaveSettings();
        }

        async function doSaveSettings() {
            if (settingsForm.enabled && !settingsForm.source) {
                MessagePlugin.warning('开启拉取前请先选择全局默认数据源');
                return;
            }
            settingsSaving.value = true;
            try {
                await request.post('/weather/settings', { ...settingsForm });
                MessagePlugin.success('设置已保存并生效');
            } finally {
                settingsSaving.value = false;
            }
        }

        // ============================================================
        // Tab3: 产区绑定与状态
        // ============================================================
        const bindingList = ref([]);
        const bindingLoading = ref(false);
        const bindingColumns = [
            { colKey: 'area_name', title: '产区', minWidth: 140 },
            { colKey: 'district', title: '行政区划', minWidth: 160 },
            { colKey: 'bound_source', title: '指定数据源', width: 170 },
            { colKey: 'last_source', title: '最近来源', width: 130 },
            { colKey: 'fetch_time', title: '最近拉取时间', width: 170 },
            { colKey: 'fetch_state', title: '状态', minWidth: 140 },
            { colKey: 'operation', title: '操作', width: 130, fixed: 'right' },
        ];

        async function fetchBindings() {
            bindingLoading.value = true;
            try {
                const res = await request.get('/weather/bindings');
                bindingList.value = res.data.data.list || [];
            } finally {
                bindingLoading.value = false;
            }
        }

        async function saveBinding(row, source) {
            try {
                await request.post('/weather/bindings', { area_id: row.area_id, source: source || '' });
                MessagePlugin.success(source ? '已指定数据源' : '已恢复默认源');
                fetchBindings();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '绑定失败');
                fetchBindings();
            }
        }

        async function refreshArea(row) {
            MessagePlugin.info('正在刷新「' + row.area_name + '」天气...');
            try {
                const res = await request.post('/weather/area/' + row.area_id + '/refresh');
                MessagePlugin.success(res.data.msg || '刷新完成');
                fetchBindings();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '刷新失败');
            }
        }

        // 手动全量拉取
        const pulling = ref(false);

        async function pullAll() {
            pulling.value = true;
            try {
                const res = await request.post('/weather/pull');
                MessagePlugin.success(res.data.msg || '拉取完成');
                fetchBindings();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '拉取失败');
            } finally {
                pulling.value = false;
            }
        }

        // 天气详情弹窗
        const weatherVisible = ref(false);
        const weatherLoading = ref(false);
        const weatherAreaName = ref('');
        const weatherDetail = ref(null);
        const forecastColumns = [
            { colKey: 'date', title: '日期', width: 110 },
            { colKey: 'text_day', title: '白天天气', minWidth: 100 },
            { colKey: 'temp_max', title: '最高温(℃)', width: 100 },
            { colKey: 'temp_min', title: '最低温(℃)', width: 100 },
        ];

        async function viewWeather(row) {
            weatherAreaName.value = row.area_name;
            weatherDetail.value = null;
            weatherVisible.value = true;
            weatherLoading.value = true;
            try {
                const res = await request.get('/weather/area/' + row.area_id);
                weatherDetail.value = res.data.data || null;
            } finally {
                weatherLoading.value = false;
            }
        }

        // ============================================================
        // Tab4: 数据查询（批次积温 + 逐日天气历史）
        // ============================================================
        const queryAreaId = ref(null);
        const queryBatchId = ref(null);
        const queryBatchOptions = ref([]);
        const queryDateRange = ref([]);
        const queryAlerts = ref([]);
        const gddData = ref(null);
        const gddError = ref('');
        const gddLoading = ref(false);
        const dailyRows = ref([]);
        const dailyLoading = ref(false);
        const dailyColumns = [
            { colKey: 'date', title: '日期', width: '12%' },
            { colKey: 'temp_max', title: '最高温(℃)', width: '12%' },
            { colKey: 'temp_min', title: '最低温(℃)', width: '12%' },
            { colKey: 'temp_avg', title: '日均温(℃)', width: '12%' },
            { colKey: 'humidity', title: '湿度(%)', width: '12%' },
            { colKey: 'precip', title: '降水(mm)', width: '12%' },
            { colKey: 'wind_scale', title: '风力(级)', width: '12%' },
            { colKey: 'text_day', title: '天气现象', width: '16%' },
        ];

        // 切换产区：重置批次/积温结果，重新加载批次下拉与逐日历史
        async function onQueryAreaChange() {
            queryBatchId.value = null;
            queryBatchOptions.value = [];
            gddData.value = null;
            gddError.value = '';
            queryAlerts.value = [];
            if (!queryAreaId.value) { dailyRows.value = []; return; }
            fetchDaily();
            fetchAreaAlerts();
            try {
                const res = await request.get('/planting-batch/list', {
                    params: { area_id: queryAreaId.value, limit: 100 },
                });
                queryBatchOptions.value = (res.data.data.list || []).map(function (b) {
                    var name = (b.batch_no || '批次' + b.id) + (b.crop_name ? ' · ' + b.crop_name : '');
                    return { id: b.id, label: name + (b.plant_date ? '（' + b.plant_date + ' 定植）' : '') };
                });
            } catch (e) { /* 拦截器已提示 */ }
        }

        // 批次积温统计（后端 status=error 时展示业务提示，如未设定植日）
        async function fetchGdd() {
            gddData.value = null;
            gddError.value = '';
            if (!queryBatchId.value) return;
            gddLoading.value = true;
            try {
                const res = await request.get('/weather/area/' + queryAreaId.value + '/gdd', {
                    params: { batch_id: queryBatchId.value },
                });
                var d = res.data.data || {};
                if (d.status === 'success') {
                    gddData.value = d;
                } else {
                    gddError.value = d.msg || '积温计算失败';
                }
            } finally {
                gddLoading.value = false;
            }
        }

        // 逐日天气历史（日期范围可选，默认全部）
        async function fetchDaily() {
            if (!queryAreaId.value) return;
            dailyLoading.value = true;
            var params = {};
            var range = queryDateRange.value || [];
            if (range[0]) params.start = range[0];
            if (range[1]) params.end = range[1];
            try {
                const res = await request.get('/weather/area/' + queryAreaId.value + '/daily', { params: params });
                dailyRows.value = res.data.data.list || [];
                nextTick(renderGddChart);
            } finally {
                dailyLoading.value = false;
            }
        }

        // 加载选中产区生效中气象预警（走天气快照/缓存，不直穿第三方API）
        async function fetchAreaAlerts() {
            if (!queryAreaId.value) { queryAlerts.value = []; return; }
            try {
                const res = await request.get('/weather/area/' + queryAreaId.value);
                queryAlerts.value = (res.data.data && res.data.data.alerts) || [];
            } catch (e) {
                queryAlerts.value = [];
            }
        }

        // ------------------------------------------------------------
        // 产区积温走势图（ECharts：日均温 + 活动/有效积温累计曲线）
        // 累计规则与后端 weather_gdd 一致：日均温>=基点时累加，缺失日跳过
        // ------------------------------------------------------------
        var gddChart = null;

        // 读主题 CSS 变量（明暗双模式下自动取当前生效色值，禁硬编码）
        function cssVar(name) {
            return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        }

        function renderGddChart() {
            var el = document.getElementById('gdd-chart');
            if (!el || !dailyRows.value.length) return;
            // Tab 切换会销毁容器 DOM，实例与当前容器不一致时重建
            if (!gddChart || gddChart.getDom() !== el) {
                if (gddChart) gddChart.dispose();
                gddChart = echarts.init(el);
            }
            var s = window.WeatherCharts.buildGddSeries(dailyRows.value, Number(settingsForm.gdd_base_temp) || 10);
            var textColor = cssVar('--app-text-secondary');
            var lineColor = cssVar('--app-border');
            gddChart.setOption({
                tooltip: { trigger: 'axis' },
                legend: { data: ['日均温', '活动积温', '有效积温'], textStyle: { color: textColor } },
                grid: { left: 56, right: 64, top: 44, bottom: 32 },
                xAxis: {
                    type: 'category', data: s.dates,
                    axisLine: { lineStyle: { color: lineColor } }, axisLabel: { color: textColor },
                },
                yAxis: [
                    // alignTicks 对齐双轴刻度，两轴都画网格线（重合不叠影），
                    // 避免图例隐藏某轴全部系列后背景网格线消失
                    { type: 'value', name: '℃', alignTicks: true, nameTextStyle: { color: textColor }, axisLabel: { color: textColor }, splitLine: { lineStyle: { color: lineColor } } },
                    { type: 'value', name: '℃·d', alignTicks: true, nameTextStyle: { color: textColor }, axisLabel: { color: textColor }, splitLine: { lineStyle: { color: lineColor } } },
                ],
                series: [
                    {
                        name: '日均温', type: 'line', smooth: true, data: s.avgs,
                        itemStyle: { color: cssVar('--app-brand-color') },
                        endLabel: { show: true, formatter: '{a}: {c}', color: cssVar('--app-text-primary') },
                    },
                    {
                        name: '活动积温', type: 'line', smooth: true, yAxisIndex: 1, data: s.actives,
                        itemStyle: { color: cssVar('--app-warning') },
                        endLabel: { show: true, formatter: '{a}: {c}', color: cssVar('--app-text-primary') },
                    },
                    {
                        name: '有效积温', type: 'line', smooth: true, yAxisIndex: 1, data: s.effectives,
                        itemStyle: { color: cssVar('--app-success') },
                        endLabel: { show: true, formatter: '{a}: {c}', color: cssVar('--app-text-primary') },
                    },
                ],
            }, true);
        }

        // 切回数据查询 Tab 时容器重建，需重新渲染已有数据
        watch(activeTab, function (val) {
            if (val === 'query') nextTick(renderGddChart);
        });

        // ============================================================
        // 初始化
        // ============================================================
        onMounted(function () {
            fetchSources();
            fetchSettings();
            fetchBindings();
            // 明暗主题切换时重取 CSS 变量重绘图表；窗口缩放时自适应
            new MutationObserver(function () {
                if (gddChart) renderGddChart();
            }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
            window.addEventListener('resize', function () {
                if (gddChart) gddChart.resize();
            });
        });

        return Object.assign({
            activeTab,
            // Tab1
            sourceList, sourceLoading, sourceColumns, enabledSources,
            installSource, uninstallSource, toggleSource, testSource,
            configVisible, configPluginTitle, configSchema, configForm, configRules, configSaving,
            openConfig, saveConfig,
            // Tab2
            settingsForm, settingsSaving, saveSettings,
            // Tab3
            bindingList, bindingLoading, bindingColumns, fetchBindings, saveBinding,
            refreshArea, pulling, pullAll,
            weatherVisible, weatherLoading, weatherAreaName, weatherDetail, forecastColumns, viewWeather,
            // Tab4
            queryAreaId, queryBatchId, queryBatchOptions, queryDateRange, queryAlerts,
            gddData, gddError, gddLoading, dailyRows, dailyLoading, dailyColumns,
            onQueryAreaChange, fetchGdd, fetchDaily,
        }, window.WeatherAlertsTab(activeTab));
    },
});
})();
