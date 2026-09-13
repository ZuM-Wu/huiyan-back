(function () {
    const { ref, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'policy_news', page: 'policy_news', setup() {
        const items = ref([]);
        const sources = ref([]);
        const activeTab = ref('policy-list');
        const loading = ref(false);
        const refreshing = ref(false);
        const errorMessage = ref('');
        const columns = [
            { colKey: 'title', title: '政策标题', minWidth: 360, cell: 'title' },
            { colKey: 'source', title: '来源', width: 150 },
            { colKey: 'published_at', title: '发布日期', width: 130 },
        ];
        const sourceColumns = [
            { colKey: 'source', title: '来源', width: 180 },
            { colKey: 'last_success_at', title: '最近成功', width: 200 },
            { colKey: 'item_count', title: '条数', width: 90 },
            { colKey: 'last_error', title: '最近错误', minWidth: 260 },
        ];
        const fetchStatus = async function () {
            loading.value = true;
            try {
                const result = await request.get('/plugins/policy_news/status');
                const data = result.data.data || {};
                items.value = data.items || [];
                sources.value = data.sources || [];
                errorMessage.value = sources.value.filter((item) => item.last_error)
                    .map((item) => item.source + '：' + item.last_error).join('；');
            } finally { loading.value = false; }
        };
        const refreshItems = async function () {
            refreshing.value = true;
            try { await request.post('/plugins/policy_news/refresh'); MessagePlugin.success('刷新完成'); await fetchStatus(); }
            finally { refreshing.value = false; }
        };
        const clearItems = async function () {
            await request.delete('/plugins/policy_news/clear');
            MessagePlugin.success('历史记录已清理');
            await fetchStatus();
        };
        const openPolicy = function (url) {
            if (typeof url === 'string' && url.trim()) {
                window.open(url, "_blank", "noopener,noreferrer");
            }
        };
        onMounted(fetchStatus);
        return { items, sources, activeTab, loading, refreshing, errorMessage, columns, sourceColumns,
            refreshItems, clearItems, openPolicy };
    }});
})();
