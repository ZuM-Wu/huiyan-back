/** JJR 厂商详情组件，所有业务请求通过公共详情上下文。 */
const template = (await import('./detail-template.js' + new URL(import.meta.url).search)).default;
const { computed, ref, reactive, nextTick, onMounted, onBeforeUnmount, watch } = Vue;
const { MessagePlugin } = TDesign;
export default {
    template, delimiters: ['[[', ']]'], props: ['device', 'context'],
    setup(props) {
        const currentDevice = computed(() => props.device), drawerVisible = ref(true), detailTab = ref('current');
        const formatChinaTime = props.context.formatTime;
            const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => (
                { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

            const identifierLoading = ref(false), photoTaking = ref(false), identifiers = ref([]);
            const snapshotFetchedAt = ref(''), snapshotError = ref('');
            const metricSelection = ref([]), metricVisibilityConfigured = ref(false);
            const metricSelectionTouched = ref(false), metricVisibilitySaving = ref(false);
            let photoTimer = null, photoResolve = null;
            let photoPollGeneration = 0, chartInstance = null, chartResizeObserver = null;
            const hasValue = (value) => value !== null && value !== undefined && value !== '';
            const displayUnit = (row) => (row && typeof row.unit === 'string' && row.unit.trim())
                || '单位未提供';
            const isSafeImageUrl = (value) => typeof value === 'string'
                && (/^https?:\/\//i.test(value) || value.indexOf('/') === 0);
            const isImageValue = (row) => row.dataType === 'image' && isSafeImageUrl(row.value);
            const metricIdentifiers = computed(() => identifiers.value.filter((item) => item.dataType !== 'image'));
            const visibleMetricIdentifiers = computed(() => {
                const selected = new Set(metricSelection.value);
                return metricIdentifiers.value.filter((item) => selected.has(item.identifier));
            });
            const imageIdentifiers = computed(() => identifiers.value.filter((item) => item.dataType === 'image'));
            const imageSignature = () => JSON.stringify(imageIdentifiers.value.map((row) => [row.identifier, row.value, row.lastUpdateTime]));
            const imageDisplayUrl = (row) => {
                if (!isImageValue(row)) return '';
                const version = row.lastUpdateTime || snapshotFetchedAt.value;
                return version ? row.value + (row.value.indexOf('?') === -1 ? '?' : '&') + 'v=' + encodeURIComponent(version) : row.value;
            };
            const metricIcon = (row) => {
                const key = ((row && row.identifier) || '').toLowerCase();
                const name = ((row && row.name) || '').toLowerCase();
                const hint = key + ' ' + name;
                if (/temp|温度/.test(hint)) return 'measurement';
                if (/humi|moist|湿度|墒情|水分/.test(hint)) return 'cloudy-rain';
                if (/lux|light|光照|太阳/.test(hint)) return 'sunny';
                if (/vol|battery|电池|电压/.test(hint)) return 'battery';
                if (/signal|4g|wifi|信号/.test(hint)) return 'wifi';
                if (/ndvi|chlor|植被|叶绿素|根系/.test(hint)) return 'tree-round-dot';
                return 'sensors-1';
            };
            const fetchIdentifiers = (refresh, quiet = false, cacheOnly = false) => {
                if (!currentDevice.value) return Promise.resolve();
                const deviceId = currentDevice.value.id; if (!quiet) identifierLoading.value = true;
                return props.context.readMetrics(refresh === true, cacheOnly === true).then((res) => {
                    if (!currentDevice.value || currentDevice.value.id !== deviceId) return false;
                    const data = res.data.data || {};
                    identifiers.value = data.list || [];
                    if (!metricVisibilityConfigured.value && !metricSelectionTouched.value) {
                        metricSelection.value = identifiers.value.filter(
                            (item) => item.dataType !== 'image' && hasValue(item.value)
                        ).map((item) => item.identifier);
                    }
                    snapshotFetchedAt.value = data.fetched_at || ''; snapshotError.value = data.last_error || '';
                    return true;
                }).catch(() => false).finally(() => {
                    if (!quiet && currentDevice.value && currentDevice.value.id === deviceId) identifierLoading.value = false;
                });
            };
            const pollPhotoResult = async (deviceId, previousSignature, generation) => {
                // 拍照后设备回传存在延迟，每 10 秒刷新一次实时数据，最多轮询 6 次（60 秒超时）后提示用户稍后刷新。
                for (let attempt = 0; attempt < 6; attempt += 1) {
                    await new Promise((resolve) => { photoResolve = resolve; photoTimer = window.setTimeout(resolve, 10000); });
                    if (generation !== photoPollGeneration || !drawerVisible.value
                        || !currentDevice.value || currentDevice.value.id !== deviceId) return null;
                    if (!await fetchIdentifiers(true, true) || generation !== photoPollGeneration) return null;
                    if (imageSignature() !== previousSignature) return true;
                }
                return false;
            };
            const takePhoto = () => {
                if (!currentDevice.value) return;
                const deviceId = currentDevice.value.id, previousSignature = imageSignature(), generation = ++photoPollGeneration;
                photoTaking.value = true;
                props.context.takePhoto().then(async () => {
                    const updated = await pollPhotoResult(deviceId, previousSignature, generation);
                    if (updated) MessagePlugin.success('照片已自动更新');
                    else if (updated === false && drawerVisible.value && currentDevice.value && currentDevice.value.id === deviceId) {
                        MessagePlugin.warning('暂未检测到新照片，请稍后刷新');
                    }
                })
                    .catch(() => {}).finally(() => { photoTaking.value = false; });
            };
            const touchMetricSelection = () => { metricSelectionTouched.value = true; };
            const onMetricSelectionChange = () => { touchMetricSelection(); };
            const selectValuedMetrics = () => {
                metricSelection.value = metricIdentifiers.value.filter(
                    (item) => hasValue(item.value)
                ).map((item) => item.identifier);
                touchMetricSelection();
            };
            const selectAllMetrics = () => {
                metricSelection.value = metricIdentifiers.value.map((item) => item.identifier); touchMetricSelection();
            };
            const clearMetricSelection = () => { metricSelection.value = []; touchMetricSelection(); };
            const saveMetricVisibility = () => {
                if (!currentDevice.value) return;
                metricVisibilitySaving.value = true;
                props.context.saveMetrics(metricSelection.value).then((res) => {
                    const saved = (res.data.data || {}).identifiers || [];
                    metricSelection.value = saved.slice();
                    metricVisibilityConfigured.value = true;
                    metricSelectionTouched.value = false;
                    props.context.metricsSaved(saved);
                    MessagePlugin.success(res.data.msg || '实时卡片设置已保存');
                }).catch(() => {}).finally(() => { metricVisibilitySaving.value = false; });
            };

            const history = reactive({ identifier: '', dateRange: [] });
            const historyList = ref([]), historyLoading = ref(false);
            const historyPagination = reactive({ current: 1, pageSize: 20, total: 0 });
            const historyColumns = [
                { colKey: 'identifier', title: '标识', width: 160 },
                { colKey: 'value', title: '值', width: 150, cell: 'historyValue' },
                { colKey: 'time', title: '采集时间', width: 200, cell: 'time' },
            ];
            const identifierOptions = computed(() => identifiers.value.filter((item) => (
                item.dataType === 'image' || metricSelection.value.indexOf(item.identifier) !== -1
            )).map((item) => ({
                label: item.name ? item.name + '（' + item.identifier + '）' : item.identifier,
                value: item.identifier,
            })));
            const selectedIdentifier = computed(() => identifiers.value.find((item) => item.identifier === history.identifier));
            const historyIsImage = computed(() => selectedIdentifier.value
                && selectedIdentifier.value.dataType === 'image');
            const historyImageItems = computed(() => historyList.value.filter((item) => isSafeImageUrl(item.value)));
            const historyImageUrls = computed(() => historyImageItems.value.map((item) => item.value));
            const chartRef = ref(null), chartVisible = ref(false);
            const disposeChart = () => {
                if (chartResizeObserver) {
                    chartResizeObserver.disconnect();
                    chartResizeObserver = null;
                }
                if (chartInstance) {
                    chartInstance.dispose();
                    chartInstance = null;
                }
            };
            const observeChartSize = (element) => {
                if (chartResizeObserver) chartResizeObserver.disconnect();
                if (!window.ResizeObserver || !element) return;
                chartResizeObserver = new window.ResizeObserver(() => {
                    if (chartInstance) chartInstance.resize();
                });
                chartResizeObserver.observe(element);
            };
            const renderChart = () => {
                const definition = selectedIdentifier.value;
                const points = historyList.value.filter((item) => hasValue(item.value))
                    .map((item) => [Date.parse(item.time), Number(item.value)])
                    .filter((item) => Number.isFinite(item[0]) && Number.isFinite(item[1]))
                    .sort((left, right) => left[0] - right[0]);
                chartVisible.value = !historyIsImage.value && points.length > 0;
                if (!chartVisible.value) {
                    if (chartInstance) chartInstance.clear();
                    return Promise.resolve();
                }
                return nextTick(() => {
                    if (!drawerVisible.value) return;
                    const chartElement = chartRef.value;
                    if (!chartElement) return;
                    if (chartInstance && chartInstance.getDom() !== chartElement) disposeChart();
                    if (!chartInstance) chartInstance = echarts.init(chartElement);
                    const css = getComputedStyle(document.documentElement);
                    const textColor = css.getPropertyValue('--td-text-color-secondary').trim();
                    const lineColor = css.getPropertyValue('--td-brand-color').trim();
                    const borderColor = css.getPropertyValue('--td-border-level-1-color').trim();
                    chartInstance.setOption({
                    animation: false,
                    grid: { left: 58, right: 24, top: 28, bottom: 52 },
                    tooltip: {
                        trigger: 'axis',
                        formatter: (params) => {
                            const item = params && params[0];
                            if (!item) return '';
                             return escapeHtml(formatChinaTime(item.value[0])) + '<br>'
                                + item.marker + escapeHtml(definition.name || definition.identifier)
                                + '：' + escapeHtml(item.value[1])
                                + ' ' + escapeHtml(displayUnit(definition));
                        },
                    },
                    xAxis: {
                        type: 'time',
                        axisLabel: {
                            color: textColor,
                            formatter: (value) => formatChinaTime(value).slice(5, 16),
                        },
                        axisPointer: { label: { formatter: (item) => formatChinaTime(item.value) } },
                        axisLine: { lineStyle: { color: borderColor } },
                    },
                    yAxis: { type: 'value', name: displayUnit(definition), axisLabel: { color: textColor }, splitLine: { lineStyle: { color: borderColor } } },
                    series: [{
                        name: definition.name || definition.identifier,
                        type: 'line', showSymbol: points.length < 30, smooth: true,
                        data: points, lineStyle: { color: lineColor }, itemStyle: { color: lineColor },
                    }],
                    }, true);
                    observeChartSize(chartElement);
                    chartInstance.resize();
                });
            };
            const fetchHistory = () => {
                if (!history.identifier || !currentDevice.value) return;
                const params = {
                    identifier: history.identifier,
                    page: historyPagination.current,
                    size: historyPagination.pageSize,
                };
                if (history.dateRange && history.dateRange.length === 2) {
                    params.start_time = history.dateRange[0] + 'T00:00:00+08:00';
                    params.end_time = history.dateRange[1] + 'T23:59:59+08:00';
                }
                historyLoading.value = true;
                props.context.readHistory(params).then((res) => {
                    const data = res.data.data || {};
                    historyList.value = (data.list || []).map((item, index) => Object.assign({
                        _rowKey: historyPagination.current + '-' + index,
                    }, item));
                    historyPagination.total = data.total || 0;
                    renderChart();
                }).catch(() => {}).finally(() => { historyLoading.value = false; });
            };
            const searchHistory = () => { historyPagination.current = 1; fetchHistory(); };
            const onHistoryPageChange = (info) => {
                historyPagination.current = info.current;
                historyPagination.pageSize = info.pageSize;
                fetchHistory();
            };
            const onDetailTabChange = (value) => {
                if (['current', 'metric-settings'].indexOf(value) !== -1
                    && !identifiers.value.length) fetchIdentifiers(false);
                if (value === 'history') {
                    const ready = identifiers.value.length ? Promise.resolve() : fetchIdentifiers(false);
                    ready.then(() => {
                        const allowed = identifierOptions.value.map((item) => item.value);
                        if (allowed.indexOf(history.identifier) === -1) history.identifier = allowed[0] || '';
                        if (history.identifier) searchHistory();
                    });
                }
            };

            watch(() => props.context.realtimeRefreshTick?.value, () => {
                if (props.context.realtimeRefreshEnabled?.value && detailTab.value === 'current'
                    && !identifierLoading.value && !photoTaking.value) {
                    fetchIdentifiers(false, true, true);
                }
            });


        onMounted(() => {
            metricVisibilityConfigured.value = Array.isArray(props.device.visible_metric_identifiers);
            metricSelection.value = metricVisibilityConfigured.value ? props.device.visible_metric_identifiers.slice() : [];
            fetchIdentifiers(false);
        });
        onBeforeUnmount(() => {
            drawerVisible.value = false; photoPollGeneration += 1;
            window.clearTimeout(photoTimer); if (photoResolve) photoResolve(); disposeChart();
        });
        return { currentDevice, detailTab, formatChinaTime, onDetailTabChange,
                identifiers, identifierLoading, photoTaking, fetchIdentifiers, takePhoto, metricIdentifiers,
                visibleMetricIdentifiers, imageIdentifiers, snapshotFetchedAt, snapshotError, metricSelection, metricVisibilitySaving, onMetricSelectionChange, selectValuedMetrics, selectAllMetrics,
                clearMetricSelection, saveMetricVisibility, hasValue, displayUnit, isImageValue, imageDisplayUrl, metricIcon, history, identifierOptions, historyList, historyLoading, historyPagination, historyColumns,
                chartRef, chartVisible, historyIsImage, historyImageItems, historyImageUrls, searchHistory,
                onHistoryPageChange,
            ...props.context.quickDetection,
        };
    },
};
