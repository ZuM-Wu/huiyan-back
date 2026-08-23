/**
 * 农户列表页面脚本 — 对应路由 /admin/farmer
 * 完整 CRUD：新增/编辑/删除/状态切换/搜索/分页
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 */
(function () {
    const { ref, reactive, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            const tableData = ref([]);
            const loading = ref(false);
            const searchKeyword = ref('');
            const searchField = ref('');
            // 搜索框占位符随字段变化
            const placeholderMap = {
                username: '搜索用户名...', nickname: '搜索昵称...',
                phone: '搜索手机号...', email: '搜索邮箱...', company: '搜索公司/农场...'
            };
            const searchPlaceholder = Vue.computed(() =>
                searchField.value ? (placeholderMap[searchField.value] || '搜索...') : '搜索农户姓名/手机号/公司...'
            );
            const pagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const columns = [
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'username', title: '用户名', width: 140, cell: 'username' },
                { colKey: 'nickname', title: '昵称', width: 120, ellipsis: true },
                { colKey: 'phone', title: '手机号', width: 140 },
                { colKey: 'email', title: '邮箱', width: 180, ellipsis: true },
                { colKey: 'company', title: '公司/农场', ellipsis: true },
                { colKey: 'region', title: '产区绑定位置', width: 170, cell: 'region' },
                { colKey: 'status', title: '状态', width: 80, cell: 'status' },
                { colKey: 'create_time', title: '注册时间', width: 180 }
            ];

            const dialogVisible = ref(false);
            const isEdit = ref(false);
            const submitLoading = ref(false);
            const formRef = ref(null);
            const formData = reactive({ username: '', password: '', nickname: '', avatar: '', phone: '', email: '', company: '', status: 1 });
            const avatarFileList = ref([]);
            const formRules = {
                username: [{ required: true, message: '请输入用户名', type: 'error' }],
                nickname: [{ required: true, message: '请输入昵称', type: 'error' }]
            };
            const editId = ref(null);

            const fetchData = () => {
                loading.value = true;
                request.get('/farmer/list', {
                    params: { page: pagination.current, limit: pagination.pageSize, keywords: searchKeyword.value, search_field: searchField.value }
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
                Object.assign(formData, { username: '', password: '', nickname: '', avatar: '', phone: '', email: '', company: '', status: 1 });
                avatarFileList.value = [];
                dialogVisible.value = true;
            };

            const openEdit = (row) => {
                isEdit.value = true;
                editId.value = row.id;
                Object.assign(formData, {
                    username: row.username, password: '', nickname: row.nickname || '',
                    avatar: row.avatar || '',
                    phone: row.phone || '', email: row.email || '', company: row.company || '',
                    status: row.status
                });
                avatarFileList.value = [];
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
                        avatar: formData.avatar,
                        phone: formData.phone, email: formData.email, company: formData.company
                    };
                    if (isEdit.value) {
                        payload.status = formData.status;
                    }
                    if (!isEdit.value || formData.password) {
                        payload.password = formData.password;
                    }
                    const promise = isEdit.value
                        ? request.put('/farmer/' + editId.value, payload)
                        : request.post('/farmer/create', payload);
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
                    body: '确定要' + actionText + '农户 [' + (row.nickname || row.username) + '] 吗？',
                    onConfirm: () => {
                        request.put('/farmer/' + row.id + '/status', { status: newStatus }).then(() => {
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
                    body: '确定要删除农户 [' + (row.nickname || row.username) + '] 吗？',
                    onConfirm: () => {
                        request.delete('/farmer/' + row.id).then(() => {
                            MessagePlugin.success('已删除');
                            instance.destroy();
                            fetchData();
                        }).catch(() => { /* 错误提示由 request.js 拦截器统一透传 */ });
                    }
                });
            };

            // 点击用户名跳转到详情页
            const goDetail = (row) => {
                var url = '/admin/farmer-detail?id=' + row.id;
                if (window.HuiYan && window.HuiYan.loadPage) {
                    window.HuiYan.loadPage(url);
                } else {
                    window.location.href = url;
                }
            };

            // 管理员快捷登录农户账号
            const doQuickLogin = (row) => {
                var instance = DialogPlugin.confirm({
                    header: '快捷登录',
                    body: '将以农户 [' + row.username + '] 的身份登录农户端，是否继续？',
                    onConfirm: () => {
                        request.post('/farmer/' + row.id + '/impersonate').then((res) => {
                            var token = res.data.data.token;
                            localStorage.setItem('farmer_token', token);
                            instance.destroy();
                            window.open('/farmer/home', '_blank');
                        }).catch(() => {
                            instance.destroy();
                        });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            /* 头像上传 */
            const uploadAvatar = (file) => {
                return new Promise((resolve) => {
                    var formDataObj = new FormData();
                    formDataObj.append('file', file.raw);
                    request.post('/upload/image', formDataObj).then((res) => {
                        if (res.data.status === 200 && res.data.data) {
                            formData.avatar = res.data.data.url;
                            MessagePlugin.success('头像上传成功');
                        }
                        resolve({ status: res.data.status === 200 ? 'success' : 'fail' });
                    }).catch(() => {
                        resolve({ status: 'fail' });
                    });
                });
            };

            onMounted(() => fetchData());
            return {
                tableData, loading, searchKeyword, searchField, searchPlaceholder, pagination, columns, fetchData, onPageChange,
                dialogVisible, isEdit, submitLoading, formRef, formData, formRules, avatarFileList,
                openCreate, openEdit, resetForm, doSubmit, toggleStatus, confirmDelete, uploadAvatar,
                goDetail, doQuickLogin
            };
        }
    });
})();
