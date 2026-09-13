/**
 * 控制台页面脚本 — 仪表盘挂件管理
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 *
 * 功能：
 *   1. 从后端拉取挂件元信息 + 显示配置 + 渲染数据
 *   2. 根据 widget_type 通用渲染（stat_card / list / chart）
 *   3. Sortable.js 拖拽排序，拖拽结束后持久化到数据库
 *   4. 挂件管理弹窗勾选/取消，即时持久化并重新渲染
 *   5. 产区积温 chart 挂件：ECharts 走势图，多产区按顺序轮播滚动
 */
(function () {
    const { ref, reactive, onMounted, nextTick } = Vue;

    HuiYan.createPage({
        setup() {
            const loading = ref(false);
            const gridRef = ref(null);
            const allWidgets = ref([]);      /* 所有挂件元信息（含 checked 状态） */
            const showWidgets = ref([]);     /* 当前显示的挂件渲染数据（有序） */
            let sortableInstance = null;

            /* ================================================================
             * 数据拉取
             * ================================================================ */

            const fetchDashboard = async () => {
                loading.value = true;
                try {
                    const res = await request.get('/widget/dashboard');
                    const data = res.data.data || {};

                    /* 构建全量挂件列表（含 checked 状态） */
                    const showSet = new Set(data.show_widgets || []);
                    allWidgets.value = (data.all_widgets || []).map(function (w) {
                        return Object.assign({}, w, { checked: showSet.has(w.name) });
                    });

                    /* 构建显示挂件的渲染数据（按 show_widgets 顺序） */
                     const dataMap = {};
                     (data.widget_data || []).forEach(function (w) {
                         dataMap[w.name] = w;
                         filterGddWidgetAreas(w);
                     });
                    showWidgets.value = (data.show_widgets || [])
                        .map(function (name) { return dataMap[name]; })
                        .filter(Boolean);

                    /* 补充 stat_card 类型的 sub 字段 */
                    enrichWidgetData();

                    /* 等待 DOM 更新后初始化拖拽与图表 */
                    await nextTick();
                    initSortable();
                    initGddChart();
                } catch (e) {
                    console.error('[Dashboard] 拉取数据失败', e);
                } finally {
                    loading.value = false;
                }
            };

            /* ================================================================
             * 拖拽排序
             * ================================================================ */

            const initSortable = function () {
                if (typeof Sortable === 'undefined') {
                    console.error('[Dashboard] Sortable.js 未加载');
                    return;
                }
                if (!gridRef.value) return;

                /* 销毁旧实例（重新渲染后重建） */
                if (sortableInstance) {
                    sortableInstance.destroy();
                    sortableInstance = null;
                }

                sortableInstance = Sortable.create(gridRef.value, {
                    handle: '.widget-drag-handle',
                    animation: 200,
                    ghostClass: 'sortable-ghost',
                    chosenClass: 'sortable-chosen',
                    onEnd: function (evt) {
                        /* DOM 已由 Sortable 移动，同步更新 Vue 数据 */
                        const oldIdx = evt.oldIndex;
                        const newIdx = evt.newIndex;
                        if (oldIdx === newIdx) return;

                        /* 重建有序数组 */
                        const arr = showWidgets.value.slice();
                        const moved = arr.splice(oldIdx, 1)[0];
                        arr.splice(newIdx, 0, moved);
                        showWidgets.value = arr;

                        /* 持久化排序 */
                        saveOrder();
                    }
                });
            };

            const saveOrder = async function () {
                const widgets = showWidgets.value.map(function (w) { return w.name; });
                try {
                    await request.put('/widget/order', { widgets: widgets });
                } catch (e) {
                    console.error('[Dashboard] 保存排序失败', e);
                }
            };

            /* ================================================================
             * 挂件管理 — 显示/隐藏切换
             * ================================================================ */

            const onToggle = async function (widget) {
                try {
                    await request.put('/widget/toggle', {
                        widget: widget.name,
                        status: widget.checked ? 1 : 0
                    });

                    /* 不替换 allWidgets（弹窗状态会异常），只刷新网格显示数据 */
                    const res = await request.get('/widget/dashboard');
                    const data = res.data.data || {};

                    const showSet = new Set(data.show_widgets || []);
                    /* 同步 allWidgets 的 checked 状态（不替换对象引用） */
                    allWidgets.value.forEach(function (w) {
                        w.checked = showSet.has(w.name);
                    });

                     const dataMap = {};
                     (data.widget_data || []).forEach(function (w) {
                         dataMap[w.name] = w;
                         filterGddWidgetAreas(w);
                     });
                    showWidgets.value = (data.show_widgets || [])
                        .map(function (name) { return dataMap[name]; })
                        .filter(Boolean);

                    enrichWidgetData();

                    await nextTick();
                    initSortable();
                    initGddChart();

                    if (window.TDesign && TDesign.MessagePlugin) {
                        TDesign.MessagePlugin.success(
                            widget.checked ? '已显示：' + widget.title : '已隐藏：' + widget.title
                        );
                    }
                } catch (e) {
                    /* 回滚 checkbox 状态 */
                    widget.checked = !widget.checked;
                    console.error('[Dashboard] 切换挂件失败', e);
                }
            };

            /* ================================================================
             * stat_card 数值格式化
             * ================================================================ */

            const formatStatValue = function (w) {
                if (!w.data) return '--';
                /* 通用取值：优先 value，其次 total（插件注册的 stat_card 挂件通用入口） */
                if (w.data.value != null) return w.data.value;
                if (w.data.total != null) return w.data.total;
                return '--';
            };

            /* 列表类型标签使用语义主题：仅改变首页展示，不修改挂件返回数据。 */
            const logTagTheme = function (widget, log) {
                const widgetName = widget && widget.name;
                const logType = String((log && log.type) || '').trim();
                if (widgetName === 'policy_news_latest') {
                    if (logType === '广西农业农村厅') return 'success';
                    if (logType === '农业农村部') return 'primary';
                    return 'default';
                }
                if (widgetName === 'knowledge_recent_records' && logType === '知识') {
                    return 'primary';
                }
                if (widgetName === 'yolo_recent_records' && logType === '识别') {
                    return 'primary';
                }
                return 'default';
            };

            /* ================================================================
             * 为 stat_card 类型挂件补充 sub 字段（当前无内置 stat_card 挂件，预留钩子）
             * ================================================================ */

            const enrichWidgetData = function () {
                showWidgets.value.forEach(function (w) {
                    if (w.widget_type !== 'stat_card' || !w.data) return;
                    /* 挂件数据自带 sub 字段时直接使用，无需额外拼装 */
                });
            };

            /* ================================================================
             * 待办事项 todo 挂件 — 点击磁贴直达对应管理页（SPA 局部加载优先）
             * ================================================================ */

            const goWidgetLog = function (item) {
                if (!item || !item.url) return;
                if (window.HuiYan && typeof HuiYan.loadPage === 'function') {
                    HuiYan.loadPage(item.url);
                } else {
                    window.location.href = item.url;
                }
            };

            const goTodo = function (item) {
                if (!item || !item.url) return;
                if (window.HuiYan && typeof HuiYan.loadPage === 'function') {
                    HuiYan.loadPage(item.url);
                } else {
                    window.location.href = item.url;
                }
            };

            /* ================================================================
             * 产区积温 chart 挂件 — ECharts 多产区轮播
             * 数据由后端 AreaGddWidget 预计算（日均温 + 活动/有效积温累计），
             * 多个产区时每 8 秒按顺序切换下一个产区的走势图
             * ================================================================ */

            let gddChart = null;      /* ECharts 实例 */
            let gddTimer = null;      /* 轮播定时器 */
            let gddIndex = 0;         /* 当前展示的产区下标 */

            /* 读主题 CSS 变量（明暗双模式自动取当前生效色值，禁硬编码） */
            const cssVar = function (name) {
                return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
            };

            const findGddWidget = function () {
                return showWidgets.value.find(function (w) {
                    return w.name === 'area_gdd' && w.data && w.data.areas && w.data.areas.length;
                });
            };

            /* 产区状态字段由不同数据源返回时可能使用不同命名，统一识别停用值。 */
            const isGddAreaEnabled = function (area) {
                if (!area || typeof area !== 'object') return false;
                const fields = ['enabled', 'is_enabled', 'active', 'is_active', 'status', 'area_status'];
                const disabledValues = ['0', '2', 'false', 'disabled', 'inactive', 'off', '停用', '禁用'];
                for (let i = 0; i < fields.length; i++) {
                    const key = fields[i];
                    if (!Object.prototype.hasOwnProperty.call(area, key) || area[key] == null) continue;
                    const value = area[key];
                    if (typeof value === 'boolean') return value;
                    if (typeof value === 'number') return value === 1;
                    const normalized = String(value).trim().toLowerCase();
                    if (disabledValues.indexOf(normalized) >= 0) return false;
                    if (['1', 'true', 'enabled', 'active', 'on', '启用', '正常'].indexOf(normalized) >= 0) return true;
                }
                return true;
            };

            const filterGddWidgetAreas = function (widget) {
                if (!widget || widget.name !== 'area_gdd' || !widget.data || !Array.isArray(widget.data.areas)) return;
                widget.data = Object.assign({}, widget.data, {
                    areas: widget.data.areas.filter(isGddAreaEnabled)
                });
            };

            /* 渲染当前下标产区的积温走势（产区名以醒目纯文本显示在头部，轮播进度由条状指示器展示） */
            const renderGddArea = function (widget) {
                const el = document.getElementById('widget-chart-area_gdd');
                if (!el) return;
                if (!gddChart || gddChart.getDom() !== el) {
                    if (gddChart) gddChart.dispose();
                    gddChart = echarts.init(el);
                }
                const areas = widget.data.areas;
                if (gddIndex >= areas.length) gddIndex = 0;
                const a = areas[gddIndex];
                const textColor = cssVar('--app-text-secondary');
                const lineColor = cssVar('--app-border');
                /* 头部产区名文本与条状指示器同步更新 */
                const label = document.getElementById('chart-label-area_gdd');
                if (label) label.textContent = a.area_name;
                renderGddIndicator(areas.length);
                gddChart.setOption({
                    tooltip: { trigger: 'axis' },
                    legend: { data: ['日均温', '活动积温', '有效积温'], top: 0, itemWidth: 14, textStyle: { color: textColor, fontSize: 11 } },
                    grid: { left: 40, right: 46, top: 30, bottom: 20 },
                    xAxis: {
                        type: 'category', data: a.dates,
                        axisLine: { lineStyle: { color: lineColor } }, axisLabel: { color: textColor, fontSize: 11 },
                    },
                    yAxis: [
                        /* alignTicks 对齐双轴刻度，两轴都画网格线，避免图例隐藏后网格消失 */
                        { type: 'value', name: '℃', alignTicks: true, nameTextStyle: { color: textColor, fontSize: 11 }, axisLabel: { color: textColor, fontSize: 11 }, splitLine: { lineStyle: { color: lineColor } } },
                        { type: 'value', name: '℃·d', alignTicks: true, nameTextStyle: { color: textColor, fontSize: 11 }, axisLabel: { color: textColor, fontSize: 11 }, splitLine: { lineStyle: { color: lineColor } } },
                    ],
                    series: [
                        { name: '日均温', type: 'line', smooth: true, data: a.avgs, itemStyle: { color: cssVar('--app-brand-color') } },
                        { name: '活动积温', type: 'line', smooth: true, yAxisIndex: 1, data: a.actives, itemStyle: { color: cssVar('--app-warning') } },
                        { name: '有效积温', type: 'line', smooth: true, yAxisIndex: 1, data: a.effectives, itemStyle: { color: cssVar('--app-success') } },
                    ],
                }, true);
            };

            /* 渲染条状翻页指示器（当前产区高亮，点击可手动切换并重置轮播计时） */
            const renderGddIndicator = function (total) {
                const box = document.getElementById('chart-indicator-area_gdd');
                if (!box) return;
                /* 单产区无需翻页，保持空容器（CSS :empty 自动隐藏） */
                if (total <= 1) { box.innerHTML = ''; return; }
                /* 数量一致时只切换 active 类，避免重建 DOM */
                if (box.children.length !== total) {
                    box.innerHTML = '';
                    for (let i = 0; i < total; i++) {
                        const bar = document.createElement('span');
                        bar.className = 'indicator-bar';
                        bar.dataset.index = i;
                        bar.addEventListener('click', function () {
                            gddIndex = Number(this.dataset.index);
                            initGddChart();
                        });
                        box.appendChild(bar);
                    }
                }
                Array.prototype.forEach.call(box.children, function (bar, i) {
                    bar.classList.toggle('active', i === gddIndex);
                });
            };

            /* 初始化图表与轮播（每次重新渲染后重建，挂件隐藏时自动停轮播） */
            const initGddChart = function () {
                if (gddTimer) { clearInterval(gddTimer); gddTimer = null; }
                const widget = findGddWidget();
                if (!widget || typeof echarts === 'undefined') return;
                renderGddArea(widget);
                if (widget.data.areas.length > 1) {
                    gddTimer = setInterval(function () {
                        gddIndex = (gddIndex + 1) % widget.data.areas.length;
                        renderGddArea(widget);
                    }, 8000);
                }
            };

            /* ================================================================
             * 生命周期
             * ================================================================ */

            onMounted(function () {
                fetchDashboard();
                /* 明暗主题切换时重取 CSS 变量重绘图表；窗口缩放时自适应 */
                new MutationObserver(function () {
                    const widget = findGddWidget();
                    if (gddChart && widget) renderGddArea(widget);
                }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
                window.addEventListener('resize', function () {
                    if (gddChart) gddChart.resize();
                });
            });

            return {
                loading,
                gridRef,
                allWidgets,
                showWidgets,
                onToggle,
                formatStatValue,
                logTagTheme,
                goTodo,
                goWidgetLog
            };
        }
    });
})();
