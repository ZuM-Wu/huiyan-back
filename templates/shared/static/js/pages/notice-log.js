/**
 * 通知日志页面脚本 — 对应路由 /admin/notice-log
 * 竖向 Tabs 分类展示（全部 / 短信 / 邮件 / 站内信管理）
 */
(function () {
    const { ref, reactive, computed, onMounted } = Vue;

    HuiYan.createPage({
        setup() {
            const tableData = ref([]);
            const loading = ref(false);
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('all', ['all', 'sms', 'email', 'inbox']));
            const filterStatus = ref(null);
            const filterRecipient = ref('');
            const dateRange = ref([]);
            const detailVisible = ref(false);
            const detailRow = ref(null);
            const detailType = ref('log');
            const pagination = reactive({ current: 1, pageSize: 20, total: 0 });

            const tabList = [
                { label: '全部日志', value: 'all', channel: '' },
                { label: '短信日志', value: 'sms', channel: 'sms' },
                { label: '邮件日志', value: 'email', channel: 'email' },
                { label: '站内信管理', value: 'inbox', channel: '' }
            ];

            const columns = [
                { colKey: 'id', title: 'ID', width: 70 },
                { colKey: 'action_key', title: '动作名称', width: 150, ellipsis: true },
                { colKey: 'channel', title: '渠道', width: 80, cell: 'channel' },
                { colKey: 'recipient', title: '接收者', width: 160, ellipsis: true },
                { colKey: 'status', title: '状态', width: 80, cell: 'status' },
                { colKey: 'send_time', title: '发送时间', width: 180 },
                { colKey: 'op', title: '操作', width: 80, cell: 'op' }
            ];

            // ---- 站内信管理状态 ----
            const inboxData = ref([]);
            const inboxLoading = ref(false);
            const inboxFilterType = ref('');
            const inboxFilterRead = ref('');
            const inboxFilterKeyword = ref('');
            const inboxSelectedKeys = ref([]);
            const inboxStats = reactive({ total: 0, unread: 0 });
            const inboxPagination = reactive({ current: 1, pageSize: 20, total: 0 });

            const inboxColumns = [
                { colKey: 'row-select', type: 'multiple', width: 50 },
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'receiver', title: '接收者', width: 140, cell: 'receiver' },
                { colKey: 'title', title: '标题', ellipsis: true, minWidth: 200 },
                { colKey: 'priority', title: '优先级', width: 90, cell: 'priority' },
                { colKey: 'is_read', title: '状态', width: 90, cell: 'is_read' },
                { colKey: 'create_time', title: '发送时间', width: 170 },
                { colKey: 'op', title: '操作', width: 130, cell: 'op', fixed: 'right' }
            ];

            const getChannel = () => {
                const tab = tabList.find(t => t.value === activeTab.value);
                return tab ? tab.channel : '';
            };

            const fetchData = () => {
                loading.value = true;
                const params = {
                    page: pagination.current,
                    limit: pagination.pageSize
                };
                const channel = getChannel();
                if (channel) params.channel = channel;
                if (filterStatus.value !== null && filterStatus.value !== '') {
                    params.status = parseInt(filterStatus.value);
                }
                if (filterRecipient.value) params.recipient = filterRecipient.value;
                if (dateRange.value && dateRange.value.length === 2) {
                    params.start_time = dateRange.value[0];
                    params.end_time = dateRange.value[1];
                }
                request.get('/notice/logs/list', { params })
                    .then((res) => {
                        const data = res.data.data || res.data;
                        tableData.value = data.list || [];
                        pagination.total = data.total || 0;
                    })
                    .catch(() => {})
                    .finally(() => { loading.value = false; });
            };

            // ---- 站内信管理方法 ----
            const fetchInbox = () => {
                inboxLoading.value = true;
                const params = { page: inboxPagination.current, limit: inboxPagination.pageSize };
                if (inboxFilterType.value) params.receiver_type = inboxFilterType.value;
                if (inboxFilterRead.value !== '') params.is_read = inboxFilterRead.value;
                if (inboxFilterKeyword.value) params.keyword = inboxFilterKeyword.value;
                request.get('/inbox/list', { params })
                    .then((res) => {
                        const data = res.data.data || res.data;
                        inboxData.value = data.list || [];
                        inboxPagination.total = data.total || 0;
                        inboxSelectedKeys.value = [];
                    })
                    .catch((error) => {
                        HuiYan.message.error(error.response?.data?.detail || '加载站内信失败');
                    })
                    .finally(() => { inboxLoading.value = false; });
                fetchInboxStats();
            };

            const fetchInboxStats = () => {
                request.get('/inbox/stats')
                    .then((res) => {
                        const data = res.data.data || res.data;
                        inboxStats.total = data.total || 0;
                        inboxStats.unread = data.unread || 0;
                    })
                    .catch((error) => {
                        HuiYan.message.error(error.response?.data?.detail || '加载站内信统计失败');
                    });
            };

            const onInboxPageChange = (pageInfo) => {
                inboxPagination.current = pageInfo.current;
                inboxPagination.pageSize = pageInfo.pageSize;
                fetchInbox();
            };

            const showInboxDetail = (row) => {
                request.get('/inbox/' + row.id)
                    .then((res) => {
                        detailRow.value = res.data.data || res.data;
                        detailType.value = 'inbox';
                        detailVisible.value = true;
                    })
                    .catch((error) => {
                        HuiYan.message.error(error.response?.data?.detail || '加载站内信详情失败');
                    });
            };

            const deleteInbox = (row) => {
                request.delete('/inbox/' + row.id)
                    .then(() => { fetchInbox(); })
                    .catch(() => {});
            };

            const bulkDeleteInbox = () => {
                if (inboxSelectedKeys.value.length === 0) return;
                request.delete('/inbox/bulk', { data: { ids: inboxSelectedKeys.value } })
                    .then(() => { fetchInbox(); })
                    .catch(() => {});
            };

            const onTabChange = () => {
                HuiYan.syncUrlTab(activeTab.value);
                if (activeTab.value === 'inbox') {
                    inboxPagination.current = 1;
                    fetchInbox();
                } else {
                    pagination.current = 1;
                    fetchData();
                }
            };

            const onPageChange = (pageInfo) => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                fetchData();
            };

            const showDetail = (row) => {
                detailRow.value = row;
                detailType.value = 'log';
                detailVisible.value = true;
            };

            // 优先取 extra.original_content（完整内容），回落到截断的 content
            const previewContent = computed(() => {
                if (!detailRow.value) return '';
                const extra = detailRow.value.extra || {};
                return extra.original_content || detailRow.value.content || '';
            });

            // 判断内容是否为 HTML（用于决定是 iframe 预览还是 pre 显示）
            const isHtmlContent = computed(() => {
                const c = previewContent.value;
                return c.trim().startsWith('<') && (c.includes('<html') || c.includes('<body') || c.includes('<div') || c.includes('<p'));
            });

            const cleanupLogs = () => {
                request.delete('/notice/logs/cleanup', { params: { before_days: 90 } })
                    .then(() => {
                        HuiYan.message.success('历史日志已清理');
                        fetchData();
                    })
                    .catch(() => { HuiYan.message.error('清理失败'); });
            };

            onMounted(() => {
                if (activeTab.value === 'inbox') {
                    fetchInbox();
                } else {
                    fetchData();
                }
            });

            return {
                tableData, loading, activeTab, filterStatus, filterRecipient,
                dateRange, detailVisible, detailRow, detailType, pagination,
                tabList, columns, fetchData, onTabChange, onPageChange,
                showDetail, cleanupLogs, isHtmlContent, previewContent,
                inboxData, inboxLoading, inboxFilterType, inboxFilterRead,
                inboxFilterKeyword, inboxSelectedKeys, inboxStats, inboxPagination,
                inboxColumns, fetchInbox, onInboxPageChange, showInboxDetail,
                deleteInbox, bulkDeleteInbox
            };
        }
    });
})();
