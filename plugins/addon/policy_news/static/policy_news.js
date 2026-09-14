(function () {
    const { ref, reactive, computed, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'policy_news', page: 'policy_news', setup() {
        const items = ref([]);
        const sources = ref([]);
        const activeTab = ref('policy-list');
        const loading = ref(false);
        const refreshing = ref(false);
        const errorMessage = ref('');
        const pagination = reactive({ current: 1, pageSize: 10, total: 0, showJumper: false });
        const columns = [
            { colKey: 'title', title: '政策标题', minWidth: 420, ellipsis: true, cell: 'title' },
            { colKey: 'source', title: '来源', width: 180, cell: 'source' },
            { colKey: 'published_at', title: '发布日期', width: 140, cell: 'published_at' },
        ];
        const sourceColumns = [
            { colKey: 'source', title: '来源', width: 180, cell: 'source' },
            { colKey: 'last_success_at', title: '最近成功', width: 200, cell: 'last_success_at' },
            { colKey: 'status', title: '抓取状态', width: 120, cell: 'status' },
            { colKey: 'item_count', title: '本次条数', width: 110, cell: 'item_count' },
            { colKey: 'last_error', title: '最近错误', minWidth: 300, ellipsis: true, cell: 'last_error' },
        ];

        const pagedItems = computed(() => {
            const start = (pagination.current - 1) * pagination.pageSize;
            return items.value.slice(start, start + pagination.pageSize);
        });
        const healthySourceCount = computed(() => sources.value.filter(
            (item) => !item.last_error && item.last_success_at,
        ).length);
        const sourceSummaryTheme = computed(() => {
            if (!sources.value.length || healthySourceCount.value < sources.value.length) return 'warning';
            return 'success';
        });
        const sourceSummaryText = computed(() => {
            if (!sources.value.length) return '来源状态待刷新';
            return `${healthySourceCount.value}/${sources.value.length} 个来源正常`;
        });

        const sourceStatusTheme = function (row) {
            if (row.last_error) return 'danger';
            return row.last_success_at ? 'success' : 'warning';
        };
        const sourceStatusText = function (row) {
            if (row.last_error) return '异常';
            return row.last_success_at ? '正常' : '未刷新';
        };
        const formatDateTime = function (value) {
            if (!value) return '尚未成功';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').slice(0, 16);
            return date.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
            }).replaceAll('/', '-');
        };
        const syncPagination = function () {
            pagination.total = items.value.length;
            const lastPage = Math.max(1, Math.ceil(pagination.total / pagination.pageSize));
            pagination.current = Math.min(pagination.current, lastPage);
        };
        const fetchStatus = async function () {
            loading.value = true;
            try {
                const result = await request.get('/plugins/policy_news/status');
                const data = result.data.data || {};
                items.value = data.items || [];
                sources.value = data.sources || [];
                syncPagination();
                errorMessage.value = sources.value.filter((item) => item.last_error)
                    .map((item) => item.source + '：' + item.last_error).join('；');
            } finally {
                loading.value = false;
            }
        };
        const refreshItems = async function () {
            refreshing.value = true;
            try {
                await request.post('/plugins/policy_news/refresh');
                MessagePlugin.success('刷新完成');
                await fetchStatus();
            } finally {
                refreshing.value = false;
            }
        };
        const clearItems = async function () {
            await request.delete('/plugins/policy_news/clear');
            MessagePlugin.success('历史记录已清理');
            pagination.current = 1;
            await fetchStatus();
        };
        const onPolicyPageChange = function (pageInfo) {
            pagination.current = pageInfo.current;
            pagination.pageSize = pageInfo.pageSize;
            syncPagination();
        };
        const openPolicy = function (url) {
            if (typeof url === 'string' && url.trim()) {
                window.open(url, '_blank', 'noopener,noreferrer');
            }
        };

        onMounted(fetchStatus);
        return {
            items, sources, activeTab, loading, refreshing, errorMessage,
            pagination, columns, sourceColumns, pagedItems, sourceSummaryTheme, sourceSummaryText,
            sourceStatusTheme, sourceStatusText, formatDateTime, refreshItems, clearItems,
            onPolicyPageChange, openPolicy,
        };
    }});
})();
