/**
 * 农业知识库插件 — 勘误审核独立后台页前端逻辑
 *
 * 从 knowledge.js 迁出的勘误审核部分，承载于共享 js/pages 目录，经
 * /static/js/pages/knowledge_corrections.js 引入（插件目录不挂 /static）。
 *
 * 功能：
 * - Tabs 状态切换（待处理/已采纳/已驳回/全部）+ 分页列表
 * - 采纳/驳回处理弹窗（含处理备注），接口复用 /knowledge/corrections
 */
(function () {
    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'knowledge', page: 'knowledge_corrections',
        setup() {
            // ---------------- 勘误列表 ----------------
            const corrData = ref([]);
            const corrLoading = ref(false);
            const corrStatus = ref(0);
            const corrPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const corrColumns = [
                { colKey: 'knowledge_title', title: '条目', minWidth: 140 },
                { colKey: 'content', title: '勘误内容', minWidth: 200 },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'create_time', title: '提交时间', width: 170 },
                { colKey: 'actions', title: '操作', width: 160, cell: 'actions' }
            ];
            const corrStatusText = (s) => (s === 1 ? '已采纳' : s === 2 ? '已驳回' : '待处理');
            const corrTagTheme = (s) => (s === 1 ? 'success' : s === 2 ? 'danger' : 'warning');

            const fetchCorrections = () => {
                corrLoading.value = true;
                request.get('/knowledge/corrections', {
                    params: {
                        status: corrStatus.value,
                        page: corrPagination.current, limit: corrPagination.pageSize
                    }
                }).then((res) => {
                    const data = res.data.data || {};
                    corrData.value = data.list || [];
                    corrPagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { corrLoading.value = false; });
            };
            const onCorrPageChange = (info) => {
                corrPagination.current = info.current;
                corrPagination.pageSize = info.pageSize;
                fetchCorrections();
            };
            // 状态 Tab 切换（待处理/已采纳/已驳回/全部），切回首页重拉
            const onCorrTabChange = () => {
                corrPagination.current = 1;
                fetchCorrections();
            };

            // ---------------- 采纳/驳回处理弹窗 ----------------
            const handleVisible = ref(false);
            const handleTitle = ref('处理勘误');
            const handleRow = ref({});
            const handleForm = reactive({ status: 1, admin_note: '' });
            const openHandle = (row, status) => {
                handleRow.value = row;
                handleForm.status = status;
                handleForm.admin_note = '';
                handleTitle.value = status === 1 ? '采纳勘误' : '驳回勘误';
                handleVisible.value = true;
            };
            const submitHandle = () => {
                request.put('/knowledge/corrections/' + handleRow.value.id + '/handle', {
                    status: handleForm.status, admin_note: handleForm.admin_note
                }).then(() => {
                    MessagePlugin.success('处理成功');
                    handleVisible.value = false;
                    fetchCorrections();
                }).catch(() => {});
            };

            onMounted(() => {
                // 先读插件配置 list_page_size 作为列表默认每页条数，再拉取首页
                request.get('/plugin/config/knowledge').then((res) => {
                    const cur = (res.data.data && res.data.data.current) || {};
                    const size = parseInt(cur.list_page_size, 10);
                    if (size > 0) { corrPagination.pageSize = Math.min(size, 100); }
                }).catch(() => {}).finally(() => { fetchCorrections(); });
            });

            return {
                corrData, corrLoading, corrStatus, corrPagination,
                corrColumns, corrStatusText, corrTagTheme, fetchCorrections,
                onCorrPageChange, onCorrTabChange,
                handleVisible, handleTitle, handleRow, handleForm, openHandle, submitHandle
            };
        }
    });
})();
