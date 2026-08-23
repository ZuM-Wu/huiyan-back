/**
 * 系统日志页面脚本 — 对应路由 /admin/log
 * 竖向 Tabs 分类展示
 */
(function () {
    const { ref, reactive, onMounted } = Vue;

    HuiYan.createPage({
        setup() {
            const tableData = ref([]);
            const loading = ref(false);
            const searchKeyword = ref('');
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('all', ['all', 'login', 'farmer', 'area', 'notice', 'plugin', 'weather', 'system', 'personal']));
            const pagination = reactive({ current: 1, pageSize: 15, total: 0 });

            // 竖向 Tab 分类列表
            const tabList = [
                { label: '全部', value: 'all', types: '' },
                { label: '管理员登录', value: 'login', types: 'login' },
                { label: '农户操作', value: 'farmer', types: 'farmer_login,farmer_create,farmer_update,farmer_delete,farmer_impersonate' },
                { label: '产区管理', value: 'area', types: 'area,plot,batch,area_map_config' },
                { label: '通知管理', value: 'notice', types: 'notice_sms,notice_email,notice_action' },
                { label: '插件操作', value: 'plugin', types: 'plugin' },
                { label: '天气服务', value: 'weather', types: 'weather,weather_task' },
                { label: '系统操作', value: 'system', types: 'config,system' },
                { label: '个人操作', value: 'personal', types: 'profile,role' }
            ];

            const columns = [
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'type', title: '类型', width: 120, cell: 'type' },
                { colKey: 'description', title: '描述', ellipsis: true },
                { colKey: 'user_name', title: '操作用户', width: 120, ellipsis: true },
                { colKey: 'ip', title: 'IP', width: 140 },
                { colKey: 'create_time', title: '时间', width: 180 }
            ];

            const getLogType = () => {
                const tab = tabList.find(t => t.value === activeTab.value);
                return tab ? tab.types : '';
            };

            const fetchData = () => {
                loading.value = true;
                request.get('/log/list', {
                    params: {
                        page: pagination.current, limit: pagination.pageSize,
                        keyword: searchKeyword.value, log_type: getLogType()
                    }
                }).then((res) => {
                    const data = res.data.data || res.data;
                    tableData.value = data.list || [];
                    pagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { loading.value = false; });
            };

            const onTabChange = () => {
                HuiYan.syncUrlTab(activeTab.value);
                pagination.current = 1;
                fetchData();
            };

            const onPageChange = (pageInfo) => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                fetchData();
            };

            onMounted(() => fetchData());
            return {
                tableData, loading, searchKeyword, activeTab, pagination,
                tabList, columns, fetchData, onTabChange, onPageChange
            };
        }
    });
})();
