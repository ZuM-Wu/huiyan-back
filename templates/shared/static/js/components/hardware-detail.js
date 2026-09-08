/** 公共硬件详情宿主：隔离设备请求、装配插件资源并提供标准回退视图。 */
(function (window) {
    'use strict';
    const { ref, shallowRef, computed, readonly, markRaw, watch, onMounted, onErrorCaptured } = Vue;
    const fallback = {
        props: ['device', 'context'], delimiters: ['[[', ']]'],
        template: `<div class="hardware-generic-detail">
            <t-alert v-if="!device.provider_available" theme="warning" message="来源不可用，显示上次成功获取的数据" />
            <t-space><t-button v-if="device.capabilities.includes('realtime')" :disabled="!device.provider_available" @click="load(true)">刷新</t-button>
                <t-button v-if="device.capabilities.includes('take_photo')" :disabled="!device.provider_available || !device.available" @click="photo">拍照</t-button></t-space>
            <t-alert v-if="error" theme="warning" :message="error" />
            <t-loading :loading="loading"><t-descriptions :column="2" bordered>
                <t-descriptions-item v-for="metric in metrics" :key="metric.identifier" :label="metric.name || metric.identifier">
                    <t-image-viewer v-if="isImage(metric)" :images="[metric.value]">
                        <template #trigger="{ open }"><t-button variant="text" @click="open">查看图片</t-button></template>
                    </t-image-viewer><span v-else>[[ metric.value ?? '暂无' ]] [[ metric.unit ]]</span>
                </t-descriptions-item></t-descriptions><t-empty v-if="!metrics.length" description="暂无缓存数据" /></t-loading>
            <template v-if="device.capabilities.includes('history')">
                <t-space><t-select v-model="identifier" :options="options" placeholder="选择历史指标" />
                    <t-button :disabled="!identifier || !device.provider_available" @click="history(1)">查询历史</t-button></t-space>
                <t-table :data="rows" :columns="columns" row-key="_key" :pagination="pagination" @page-change="pageChange" />
            </template>
        </div>`,
        setup(props) {
            const metrics = ref([]), rows = ref([]), identifier = ref(''), loading = ref(false), error = ref('');
            const pagination = ref({ current: 1, pageSize: 20, total: 0 });
            const columns = [{ colKey: 'identifier', title: '指标' }, { colKey: 'value', title: '值' }, { colKey: 'time', title: '时间' }];
            const options = computed(() => metrics.value.map((item) => ({ value: item.identifier, label: item.name || item.identifier })));
            const isImage = (item) => item.dataType === 'image' && typeof item.value === 'string' && /^(https?:\/\/|\/(?!\/))/i.test(item.value);
            const load = async (refresh = false, cacheOnly = false) => {
                if (loading.value) return;
                loading.value = true; error.value = '';
                try { metrics.value = (await props.context.readMetrics(refresh, cacheOnly)).data.data.list || []; }
                catch (exc) { error.value = exc.response?.data?.detail || '无法读取设备数据'; }
                finally { loading.value = false; }
            };
            const history = async (page) => {
                try {
                    const data = (await props.context.readHistory({ identifier: identifier.value, page, size: pagination.value.pageSize })).data.data;
                    rows.value = data.list.map((item, index) => ({ ...item, _key: page + '-' + index }));
                    pagination.value.current = page; pagination.value.total = data.total;
                } catch (exc) { error.value = exc.response?.data?.detail || '历史数据获取失败'; }
            };
            const pageChange = (page) => { pagination.value.pageSize = page.pageSize; history(page.current); };
            const photo = async () => { try { await props.context.takePhoto(); await load(true); } catch (_) { error.value = '拍照失败'; } };
            watch(() => props.context.realtimeRefreshTick?.value, () => {
                if (props.context.realtimeRefreshEnabled?.value) load(false, true);
            });
            onMounted(() => load(false));
            return { metrics, rows, identifier, loading, error, pagination, columns, options, load, history, pageChange, photo, isImage };
        },
    };

    function create(options) {
        const component = shallowRef(null), loading = ref(false), error = ref(''), key = ref(0);
        const device = shallowRef(null), context = shallowRef(null);
        let generation = 0, controller = null, styles = [], styleCancels = [];
        const cancelled = () => new DOMException('详情已关闭或切换', 'AbortError');
        const dispose = () => {
            generation += 1;
            if (controller) controller.abort();
            controller = null; component.value = null;
            styleCancels.forEach((cancel) => cancel()); styleCancels = [];
            styles.forEach((style) => style.remove()); styles = [];
        };
        const isCurrent = (token) => generation === token && options.drawerVisible.value;
        const resourceUrl = (value, owner) => {
            const url = new URL(value, window.location.origin);
            if (url.origin !== window.location.origin || !url.pathname.startsWith('/plugin-assets/' + owner + '/')) {
                throw new Error('详情资源不属于当前来源');
            }
            return url.href;
        };
        const loadStyle = (url, token) => new Promise((resolve, reject) => {
            const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = url;
            let timer;
            const finish = (failure) => {
                window.clearTimeout(timer); link.onload = null; link.onerror = null;
                if (failure) reject(failure); else resolve();
            };
            link.onload = () => finish(isCurrent(token) ? null : cancelled());
            link.onerror = () => finish(new Error('详情样式加载失败'));
            timer = window.setTimeout(() => finish(new Error('详情样式加载超时')), 10000);
            styleCancels.push(() => finish(cancelled())); styles.push(link); document.head.appendChild(link);
        });
        const open = async (row) => {
            dispose(); const token = generation; controller = new AbortController();
            let signal = controller.signal;
            loading.value = true; error.value = ''; key.value += 1;
            const base = '/hardware-devices/' + row.id;
            const check = () => { if (!isCurrent(token)) throw cancelled(); };
            const update = (value) => {
                check(); device.value = readonly(value);
                options.currentDevice.value = { ...value };
            };
            update({ ...row });
            const readContext = async () => {
                const response = await options.request.get(base + '/detail-context', { signal, skipAutoError: true });
                check(); update(response.data.data.device); return response.data.data;
            };
            const execute = async (method, path, body, params, live = false) => {
                check();
                if (live) {
                    const state = await readContext();
                    if (!state.provider.available) {
                        controller.abort(); controller = new AbortController(); signal = controller.signal;
                        styles.forEach((style) => style.remove()); styles = [];
                        component.value = markRaw(fallback); error.value = '硬件来源已停用，已切换为缓存详情';
                        throw new Error('硬件来源不可用');
                    }
                }
                const config = { signal, params, skipAutoError: true };
                const response = method === 'get' ? await options.request.get(base + path, config)
                    : await options.request[method](base + path, body, config);
                check(); return response;
            };
            context.value = Object.freeze({
                formatTime: options.formatTime,
                readMetrics: (refresh, cacheOnly) => execute('get', '/identifiers', null, {
                    refresh: refresh === true,
                    cache_only: cacheOnly === true,
                }, refresh === true),
                realtimeRefreshTick: options.realtimeRefreshTick,
                realtimeRefreshEnabled: options.realtimeRefreshEnabled,
                readHistory: (params) => execute('get', '/history', null, params, true),
                saveMetrics: (identifiers) => execute('put', '/metric-visibility', { identifiers }),
                takePhoto: () => execute('post', '/take-photo', null, null, true),
                metricsSaved: (identifiers) => { check(); update({ ...device.value, visible_metric_identifiers: identifiers.slice() }); },
                quickDetection: options.quickDetection.bindings,
            });
            try {
                const state = await readContext(), view = state.provider.detail_view;
                if (!view || !state.provider.available) { component.value = markRaw(fallback); return; }
                const owner = state.provider.provider_id;
                const entry = resourceUrl(view.entry, owner);
                await Promise.all((view.styles || []).map((url) => loadStyle(resourceUrl(url, owner), token)));
                const module = await import(entry); check();
                if (!module.default || typeof module.default !== 'object') throw new Error('详情组件未正确导出');
                component.value = markRaw(module.default);
            } catch (exc) {
                if (isCurrent(token)) {
                    console.warn('硬件详情加载失败:', exc.message);
                    styles.forEach((style) => style.remove()); styles = [];
                    error.value = '厂商详情加载失败，已显示公共详情'; component.value = markRaw(fallback);
                }
            } finally { if (isCurrent(token)) loading.value = false; }
        };
        onErrorCaptured((exc) => {
            console.warn('硬件详情运行失败:', exc.message);
            if (component.value !== fallback) { error.value = '厂商详情运行失败，已显示公共详情'; component.value = markRaw(fallback); return false; }
        });
        watch(options.drawerVisible, (visible) => { if (!visible) dispose(); });
        return { open, dispose, bindings: {
            detailComponent: component, detailLoading: loading, detailError: error, detailKey: key,
            detailDevice: device, detailContext: context, retryDetail: () => open(options.currentDevice.value),
        } };
    }
    window.HuiYanHardwareDetail = { create };
})(window);
