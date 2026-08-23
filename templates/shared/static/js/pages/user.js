/**
 * 管理员管理页面脚本 — 对应路由 /admin/user
 * 含两个 Tab：管理员列表 + 角色管理
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 */
(function () {
    const { ref, reactive, computed, onMounted, watch } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('admin', ['admin', 'role']));
            watch(activeTab, v => HuiYan.syncUrlTab(v));

            // ==================== 管理员列表 ====================
            const tableData = ref([]);
            const loading = ref(false);
            const searchKeyword = ref('');
            const pagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const columns = [
                { colKey: 'username', title: '用户名', ellipsis: true },
                { colKey: 'nickname', title: '昵称', ellipsis: true },
                { colKey: 'email', title: '邮箱', ellipsis: true },
                { colKey: 'roles', title: '所属角色', width: 120, ellipsis: true },
                { colKey: 'status', title: '状态', width: 80, cell: 'status' },
                { colKey: 'last_login_ip', title: '最后登录IP', width: 140, ellipsis: true },
                { colKey: 'create_time', title: '创建时间', width: 180 },
                { colKey: 'actions', title: '操作', width: 200, cell: 'actions' }
            ];

            const dialogVisible = ref(false);
            const isEdit = ref(false);
            const submitLoading = ref(false);
            const formRef = ref(null);
            const formData = reactive({ username: '', password: '', nickname: '', email: '', role_id: 1 });
            const formRules = {
                username: [{ required: true, message: '请输入用户名', type: 'error' }],
                nickname: [{ required: true, message: '请输入昵称', type: 'error' }]
            };
            const editId = ref(null);

            const fetchData = () => {
                loading.value = true;
                request.get('/admin/list', {
                    params: { page: pagination.current, limit: pagination.pageSize, keywords: searchKeyword.value }
                }).then((res) => {
                    const data = request.unwrapData(res) || {};
                    tableData.value = data.list || [];
                    pagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { loading.value = false; });
            };

            const onPageChange = (pageInfo) => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                fetchData();
            };

            const openCreate = () => {
                isEdit.value = false;
                editId.value = null;
                Object.assign(formData, { username: '', password: '', nickname: '', email: '', role_id: 1, status: 1 });
                dialogVisible.value = true;
            };

            const openEdit = (row) => {
                isEdit.value = true;
                editId.value = row.id;
                Object.assign(formData, {
                    username: row.username, password: '', nickname: row.nickname || '',
                    email: row.email || '', role_id: row.role_id || 1
                });
                dialogVisible.value = true;
            };

            const resetForm = () => { if (formRef.value) formRef.value.reset(); };

            const doSubmit = () => {
                if (!formRef.value) return;
                formRef.value.validate().then((valid) => {
                    if (valid !== true) {
                        submitLoading.value = false;
                        return;
                    }
                    submitLoading.value = true;
                    const payload = {
                        username: formData.username, nickname: formData.nickname,
                        email: formData.email, role_id: formData.role_id
                    };
                    if (!isEdit.value || formData.password) {
                        payload.password = formData.password;
                    }
                    const promise = isEdit.value
                        ? request.put('/admin/' + editId.value, payload)
                        : request.post('/admin/create', payload);
                    promise.then(() => {
                        MessagePlugin.success(isEdit.value ? '修改成功' : '创建成功');
                        submitLoading.value = false;
                        dialogVisible.value = false;
                        fetchData();
                    }).catch((err) => {
                        console.error('请求错误:', err);
                        submitLoading.value = false;
                    });
                });
            };

            const toggleStatus = (row) => {
                const newStatus = row.status === 1 ? 0 : 1;
                const actionText = newStatus === 1 ? '启用' : '禁用';
                const instance = DialogPlugin.confirm({
                    header: '确认' + actionText,
                    body: '确定要' + actionText + '管理员 [' + row.username + '] 吗？',
                    onConfirm: () => {
                        request.put('/admin/' + row.id + '/status', { status: newStatus }).then(() => {
                            MessagePlugin.success(actionText + '成功');
                            instance.destroy();
                            fetchData();
                        }).catch(() => { /* 错误提示由 request.js 拦截器统一透传 */ });
                    }
                });
            };

            const confirmDelete = (row) => {
                const instance = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '确定要删除管理员 [' + row.username + '] 吗？',
                    onConfirm: () => {
                        request.delete('/admin/' + row.id).then(() => {
                            MessagePlugin.success('已删除');
                            instance.destroy();
                            fetchData();
                        }).catch(() => { /* 错误提示由 request.js 拦截器统一透传 */ });
                    }
                });
            };

            // ==================== 角色管理 ====================
            const roleData = ref([]);
            const roleLoading = ref(false);
            /* 管理员弹窗"所属角色"下拉选项：复用角色列表数据 */
            const roleOptions = computed(() => roleData.value.map(r => ({ label: r.name, value: r.id })));
            const roleColumns = [
                { colKey: 'name', title: '角色名称', ellipsis: true },
                { colKey: 'description', title: '描述', ellipsis: true },
                { colKey: 'is_system', title: '类型', width: 100, cell: 'is_system' },
                { colKey: 'create_time', title: '创建时间', width: 180 },
                { colKey: 'actions', title: '操作', width: 140, cell: 'actions' }
            ];

            const roleDialogVisible = ref(false);
            const roleIsEdit = ref(false);
            const roleSubmitLoading = ref(false);
            const roleFormRef = ref(null);
            const roleForm = reactive({ name: '', description: '', auth: [] });
            const roleRules = { name: [{ required: true, message: '请输入角色名称', type: 'error' }] };
            const roleEditId = ref(null);
            const treeRef = ref(null);
            const expandAll = ref(true);
            const expandedKeys = ref([]);
            const checkAll = ref(false);
            const treeFilter = ref('');

            /* 权限树数据（从 /tree 接口获取已含 children） */
            const permTreeRaw = ref([]);
            const permTreeData = computed(() => permTreeRaw.value);

            /* 收集所有含子节点的父节点 ID（用于"展开全部"） */
            const collectParentIds = (nodes) => {
                const ids = [];
                const walk = (list) => {
                    list.forEach(n => {
                        if (n.children && n.children.length) {
                            ids.push(n.id);
                            walk(n.children);
                        }
                    });
                };
                walk(nodes);
                return ids;
            };

            /* 树形交互：展开/折叠（通过受控 expanded 驱动，expand-all 仅初始生效故改用此方案） */
            const toggleExpand = () => {
                if (expandAll.value) {
                    expandedKeys.value = [];
                } else {
                    expandedKeys.value = collectParentIds(permTreeRaw.value);
                }
                expandAll.value = !expandAll.value;
            };

            /* 用户手动展开/收起单个节点时同步受控值，并刷新按钮文案状态 */
            const onExpand = (value) => {
                expandedKeys.value = value;
                expandAll.value = value.length >= collectParentIds(permTreeRaw.value).length;
            };

            /* 树形交互：全选/全不选 */
            const toggleCheckAll = () => {
                if (checkAll.value) {
                    roleForm.auth = [];
                } else {
                    // 收集所有节点 ID
                    const allIds = [];
                    const collect = (nodes) => {
                        nodes.forEach(n => {
                            allIds.push(n.id);
                            if (n.children) collect(n.children);
                        });
                    };
                    collect(permTreeRaw.value);
                    roleForm.auth = allIds;
                }
                checkAll.value = !checkAll.value;
            };

            /* 树形交互：搜索过滤 */
            const filterTreeNode = (node) => {
                if (!treeFilter.value) return true;
                return node.title.includes(treeFilter.value);
            };

            const onTreeFilter = () => {
                // 触发重新渲染
            };

            const fetchRoles = () => {
                roleLoading.value = true;
                request.get('/role/list').then((res) => {
                    const data = res.data.data || res.data || {};
                    roleData.value = data.list || [];
                }).catch(() => {}).finally(() => { roleLoading.value = false; });
            };

            const openRoleCreate = () => {
                roleIsEdit.value = false;
                roleEditId.value = null;
                Object.assign(roleForm, { name: '', description: '', auth: [] });
                roleDialogVisible.value = true;
            };

            const openRoleEdit = (row) => {
                roleIsEdit.value = true;
                roleEditId.value = row.id;
                /* 获取角色详情以加载已分配的权限ID */
                request.get('/role/' + row.id).then(res => {
                    const detail = res.data.data || res.data || {};
                    Object.assign(roleForm, {
                        name: detail.name,
                        description: detail.description || '',
                        auth: detail.auth || []
                    });
                    roleDialogVisible.value = true;
                }).catch(() => {
                    Object.assign(roleForm, { name: row.name, description: row.description || '', auth: [] });
                    roleDialogVisible.value = true;
                });
            };

            const resetRoleForm = () => { if (roleFormRef.value) roleFormRef.value.reset(); };

            const doRoleSubmit = () => {
                if (!roleFormRef.value) return;
                roleFormRef.value.validate().then((valid) => {
                    if (valid !== true) {
                        roleSubmitLoading.value = false;
                        return;
                    }
                    roleSubmitLoading.value = true;
                    const payload = {
                        name: roleForm.name,
                        description: roleForm.description,
                        auth: roleForm.auth || []
                    };
                    const promise = roleIsEdit.value
                        ? request.put('/role/' + roleEditId.value, payload)
                        : request.post('/role/create', payload);
                    promise.then(() => {
                        MessagePlugin.success(roleIsEdit.value ? '修改成功' : '创建成功');
                        roleSubmitLoading.value = false;
                        roleDialogVisible.value = false;
                        fetchRoles();
                    }).catch((err) => {
                        console.error('请求错误:', err);
                        roleSubmitLoading.value = false;
                    });
                });
            };

            const confirmRoleDelete = (row) => {
                const instance = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '确定要删除角色 [' + row.name + '] 吗？',
                    onConfirm: () => {
                        request.delete('/role/' + row.id).then(() => {
                            MessagePlugin.success('已删除');
                            instance.destroy();
                            fetchRoles();
                        }).catch(() => { /* 错误提示由 request.js 拦截器统一透传 */ });
                    }
                });
            };

            /* 加载权限树（从 /tree 接口获取已组装好的树） */
            const fetchPermissions = () => {
                request.get('/permission/tree').then(res => {
                    const data = res.data.data || res.data || {};
                    permTreeRaw.value = data.list || [];
                    /* 默认展开全部：初始化受控展开键为所有父节点 */
                    expandedKeys.value = collectParentIds(permTreeRaw.value);
                    expandAll.value = true;
                }).catch(() => {});
            };

            onMounted(() => { fetchData(); fetchRoles(); fetchPermissions(); });
            return {
                activeTab,
                // 管理员
                tableData, loading, searchKeyword, pagination, columns, fetchData, onPageChange,
                dialogVisible, isEdit, submitLoading, formRef, formData, formRules, roleOptions,
                openCreate, openEdit, resetForm, doSubmit, toggleStatus, confirmDelete,
                // 角色
                roleData, roleLoading, roleColumns,
                roleDialogVisible, roleIsEdit, roleSubmitLoading, roleFormRef, roleForm, roleRules,
                permTreeData, treeRef, expandAll, expandedKeys, checkAll, treeFilter,
                toggleExpand, toggleCheckAll, onExpand, filterTreeNode, onTreeFilter,
                openRoleCreate, openRoleEdit, resetRoleForm, doRoleSubmit, confirmRoleDelete
            };
        }
    });
})();
