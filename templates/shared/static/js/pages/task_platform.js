/** 任务扩展平台目录与可靠事件运维状态。 */
(function () {
    const { ref, reactive, computed } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    function create(request) {
        const capabilityTab = ref('subscriptions');
        const definitions = ref([]);
        const events = ref([]);
        const subscriptions = ref([]);
        const outboxData = ref([]);
        const overview = reactive({ wait: 0, exec: 0, paused: 0, dead: 0, cancelled: 0 });
        const overviewItems = computed(() => [
            { label: '等待中', value: overview.wait },
            { label: '执行中', value: overview.exec },
            { label: '已暂停', value: overview.paused },
            { label: '死信', value: overview.dead },
        ]);
        const eventColumns = [
            { colKey: 'name', title: '事件名称', minWidth: 200 },
            { colKey: 'title', title: '中文名称', minWidth: 150 },
            { colKey: 'category', title: '分类', width: 100 },
            { colKey: 'delivery', title: '投递', width: 100 },
            { colKey: 'owner', title: '发布方', width: 130 },
            { colKey: 'version', title: '版本', width: 70 },
        ];
        const subscriptionColumns = [
            { colKey: 'event_name', title: '事件名称', minWidth: 200 },
            { colKey: 'owner', title: '监听方', width: 140 },
            { colKey: 'title', title: '用途', minWidth: 180 },
            { colKey: 'timeout_seconds', title: '超时(秒)', width: 90 },
            { colKey: 'max_attempts', title: '最大尝试', width: 90 },
            { colKey: 'concurrency', title: '并发', width: 70 },
        ];
        const definitionColumns = [
            { colKey: 'name', title: '定义名称', minWidth: 200 },
            { colKey: 'title', title: '中文名称', minWidth: 150 },
            { colKey: 'owner', title: '所有者', width: 130 },
            { colKey: 'group', title: '队列组', width: 120 },
            { colKey: 'timeout_seconds', title: '超时(秒)', width: 90 },
            { colKey: 'max_attempts', title: '最大尝试', width: 90 },
            { colKey: 'concurrency', title: '并发', width: 70 },
        ];
        const outboxColumns = [
            { colKey: 'id', title: 'ID', width: 80 },
            { colKey: 'event_name', title: '事件名称', minWidth: 200 },
            { colKey: 'owner', title: '发布方', width: 130 },
            { colKey: 'status', title: '状态', width: 110 },
            { colKey: 'correlation_id', title: '关联标识', minWidth: 150 },
            { colKey: 'create_time', title: '创建时间', width: 170 },
            { colKey: 'dispatched_at', title: '分发时间', width: 170 },
            { colKey: 'op', title: '操作', width: 80, cell: 'op', fixed: 'right' },
        ];

        async function fetchDefinitions() {
            try {
                const res = await request.get('/task-queue/definitions');
                definitions.value = res.data.data || [];
            } catch (error) {
                MessagePlugin.error('加载任务定义失败');
            }
        }

        async function fetchOverview() {
            try {
                const res = await request.get('/task-queue/stats');
                Object.assign(overview, res.data.data || {});
            } catch (error) {
                MessagePlugin.error('加载任务概览失败');
            }
        }

        async function fetchEvents() {
            try {
                const res = await request.get('/task-queue/events');
                events.value = res.data.data.definitions || [];
                subscriptions.value = res.data.data.subscriptions || [];
            } catch (error) {
                MessagePlugin.error('加载事件目录失败');
            }
        }

        async function fetchOutbox() {
            try {
                const res = await request.get('/task-queue/outbox', { params: { limit: 100 } });
                outboxData.value = res.data.data.list || [];
            } catch (error) {
                MessagePlugin.error('加载可靠事件失败');
            }
        }

        function replayEvent(row) {
            DialogPlugin.confirm({
                header: '确认重放',
                body: '确认重放事件 #' + row.id + '？当前订阅者会收到新的独立投递。',
                onConfirm: async () => {
                    try {
                        await request.post('/task-queue/outbox/' + row.id + '/replay');
                        MessagePlugin.success('事件已进入待分发状态');
                        fetchOutbox();
                    } catch (error) {
                        MessagePlugin.error(error.response?.data?.detail || '重放失败');
                    }
                },
            });
        }

        return {
            capabilityTab, definitions, events, subscriptions, outboxData,
            overviewItems, eventColumns, subscriptionColumns,
            definitionColumns, outboxColumns, fetchDefinitions,
            fetchOverview, fetchEvents, fetchOutbox, replayEvent,
        };
    }

    window.HuiYanTaskPlatform = { create };
})();
