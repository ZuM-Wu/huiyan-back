/**
 * 管理端主列表字段可见性组件。
 * 页面通过插槽接收 visibleColumns，组件按当前管理员保存的 hidden key
 * 过滤列，并保证 required 列始终可见。
 */
(function (window) {
    'use strict';

    if (!window.Vue) return;

    var states = Object.create(null);
    var normalizeColumns = function (columns) {
        return (columns || []).filter(function (column) { return column && column.colKey; });
    };
    var isRequiredColumn = function (column, index) {
        return index === 0 || column.required === true
            || /^(actions?|operation|record_actions)$/.test(column.colKey);
    };
    var ensureState = function (tableKey, columns) {
        var normalized = normalizeColumns(columns);
        var state = states[tableKey];
        if (state) {
            state.columns = normalized;
            return state;
        }
        state = {
            columns: normalized,
            hidden: Vue.ref(null),
            loading: Vue.ref(false),
            loaded: Vue.ref(false),
            saveTimer: null,
        };
        states[tableKey] = state;
        state.hidden.value = normalized.filter(function (column, index) {
            return column.defaultVisible === false && !isRequiredColumn(column, index);
        }).map(function (column) { return column.colKey; });
        if (window.request) {
            state.loading.value = true;
            window.request.get('/profile/preferences/table-columns/' + encodeURIComponent(tableKey), {
                skipAutoError: true,
            }).then(function (response) {
                var body = response && response.data ? response.data : {};
                var data = body.data || body;
                if (data && data.configured && Array.isArray(data.hidden)) {
                    state.hidden.value = data.hidden.slice();
                }
            }).catch(function () {
                // 读取失败时保留列元数据提供的默认值。
            }).finally(function () {
                state.loaded.value = true;
                state.loading.value = false;
            });
        } else {
            state.loaded.value = true;
        }
        return state;
    };
    var getVisibleColumns = function (tableKey, columns) {
        var normalized = normalizeColumns(columns);
        var state = ensureState(tableKey, normalized);
        var hidden = new Set(state.hidden.value || []);
        return normalized.filter(function (column, index) {
            return !hidden.has(column.colKey) || isRequiredColumn(column, index);
        });
    };
    window.HuiYanTableColumns = {
        ensure: ensureState,
        getVisible: getVisibleColumns,
    };

    var TableColumnVisibility = {
        name: 'TableColumnVisibility',
        props: {
            tableKey: { type: String, required: true },
            columns: { type: Array, default: function () { return []; } },
        },
        emits: ['columns-change'],
        setup: function (props, context) {
            var computed = Vue.computed;
            var onMounted = Vue.onMounted;
            var watch = Vue.watch;
            var state = ensureState(props.tableKey, props.columns);
            var loading = state.loading;
            var initialized = state.loaded;
            var requestClient = window.request;
            var message = window.TDesign && window.TDesign.MessagePlugin;
            var normalizedColumns = computed(function () {
                return (props.columns || []).filter(function (column) { return column && column.colKey; });
            });
            var visibleKeys = computed(function () {
                var hidden = new Set(state.hidden.value || []);
                return normalizedColumns.value.filter(function (column, index) {
                    return !hidden.has(column.colKey) || isRequiredColumn(column, index);
                }).map(function (column) { return column.colKey; });
            });
            var requiredKeys = computed(function () {
                return normalizedColumns.value.filter(function (column, index) {
                    return isRequiredColumn(column, index);
                })
                    .map(function (column) { return column.colKey; });
            });
            var defaultKeys = computed(function () {
                return normalizedColumns.value.filter(function (column, index) {
                    return column.defaultVisible !== false || isRequiredColumn(column, index);
                })
                    .map(function (column) { return column.colKey; });
            });
            var visibleColumns = computed(function () {
                var allowed = new Set(visibleKeys.value);
                return normalizedColumns.value.filter(function (column) {
                    return allowed.has(column.colKey) || requiredKeys.value.indexOf(column.colKey) !== -1;
                });
            });
            var availableKeys = computed(function () {
                return normalizedColumns.value.map(function (column) { return column.colKey; });
            });
            var applyKeys = function (keys) {
                var available = new Set(availableKeys.value);
                var required = new Set(requiredKeys.value);
                state.hidden.value = availableKeys.value.filter(function (key) {
                    return !keys.includes(key) && !required.has(key);
                });
            };
            var unwrap = function (response) {
                var body = response && response.data ? response.data : {};
                return body.data || body;
            };
            var save = function () {
                if (!initialized.value || !requestClient) return;
                if (state.saveTimer) clearTimeout(state.saveTimer);
                state.saveTimer = setTimeout(function () {
                    requestClient.put(
                        '/profile/preferences/table-columns/' + encodeURIComponent(props.tableKey),
                        { hidden: state.hidden.value, available: availableKeys.value, required: requiredKeys.value },
                        { skipAutoError: true }
                    ).catch(function () {
                        if (message) message.error('字段显示设置保存失败');
                    });
                }, 250);
            };
            var toggle = function (column, checked) {
                if (requiredKeys.value.indexOf(column.colKey) !== -1) return;
                var next = visibleKeys.value.filter(function (key) { return key !== column.colKey; });
                if (checked) next.push(column.colKey);
                applyKeys(next);
                context.emit('columns-change', visibleColumns.value.slice());
                save();
            };
            onMounted(function () {
                context.emit('columns-change', visibleColumns.value.slice());
            });
            watch(state.hidden, function () {
                context.emit('columns-change', visibleColumns.value.slice());
            });
            return {
                normalizedColumns: normalizedColumns,
                requiredKeys: requiredKeys,
                visibleKeys: visibleKeys,
                visibleColumns: visibleColumns,
                loading: loading,
                toggle: toggle,
            };
        },
        template: `
            <t-popup trigger="hover" placement="bottom-right" :show-arrow="true" :hide-delay="240">
                <t-tooltip content="列设置">
                    <t-button shape="square" theme="default" class="hy-table-column-visibility-button">
                        <template #icon><t-icon name="setting"></t-icon></template>
                    </t-button>
                </t-tooltip>
                <template #content>
                    <section class="hy-table-column-visibility-panel">
                        <div class="hy-table-column-visibility-title">显示字段</div>
                        <div v-if="loading" class="hy-table-column-visibility-loading">正在读取设置...</div>
                        <div v-else class="hy-table-column-visibility-options">
                            <t-checkbox v-for="column in normalizedColumns" :key="column.colKey"
                                :checked="visibleKeys.indexOf(column.colKey) !== -1"
                                :disabled="requiredKeys.indexOf(column.colKey) !== -1"
                                @change="toggle(column, $event)">
                                [[ column.title ]]
                            </t-checkbox>
                        </div>
                    </section>
                </template>
            </t-popup>
            <slot :visible-columns="visibleColumns" :visible-keys="visibleKeys"></slot>
        `,
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['table-column-visibility'] = TableColumnVisibility;
})(window);
