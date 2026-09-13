(function () {
        const { ref, onMounted } = Vue;
        HuiYanFarmer.createPluginPage({ plugin: 'policy_news', page: 'policy_news_index', setup() {
        const items = ref([]);
        const openPolicy = function (url) {
            if (typeof url === 'string' && url.trim()) {
                window.open(url, "_blank", "noopener,noreferrer");
            }
        };
        onMounted(async function () {
            try {
                const result = await HuiYanFarmer.request.get('/plugins/policy_news/latest');
                items.value = (result.data && result.data.items) || [];
            } catch (error) { items.value = []; }
        });
        return { items, openPolicy };
    }});
})();
