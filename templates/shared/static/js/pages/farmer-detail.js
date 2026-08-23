/**
 * 农户详情页面脚本 — 对应路由 /admin/farmer-detail?id=X
 * 功能：查看详情 / 编辑保存 / 删除 / 停用启用 / 用户快捷切换
 * Composition API + ES6 + HuiYan.createPage
 */
(function () {
    const { ref, reactive, watch, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            const loading = ref(false);
            const saving = ref(false);
            const detail = ref(null);
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（保留 ?id= 参数，刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('profile', ['profile', 'login']));
            watch(activeTab, (val) => { HuiYan.syncUrlTab(val); });
            const farmerList = ref([]);
            const switcherId = ref(null);
            const searchField = ref('all');
            const certStatus = ref(null);
            const avatarFileList = ref([]);

            /* 可编辑表单 */
            const editForm = reactive({
                nickname: '', avatar: '', phone: '', email: '', company: '',
                address: '', remark: '', country: '', language: '',
                password: ''
            });

            /* 操作日志表列定义（ID / 详情 / 时间 / IP / 操作人） */
            const opLogColumns = [
                { colKey: 'id', title: 'ID', width: 80 },
                { colKey: 'description', title: '详情',
                    cell: (h, { row }) => {
                        return row.description || '--';
                    }
                },
                { colKey: 'time', title: '时间', width: 170 },
                { colKey: 'ip', title: 'IP 地址', width: 150 },
                { colKey: 'operator', title: '操作人', width: 120 }
            ];

            /* 个人资料 Tab 内的精简日志列（仅 IP + 登录时间） */
            const shortLogColumns = [
                { colKey: 'ip', title: 'IP 地址' },
                { colKey: 'time', title: '登录时间' }
            ];

            /* 操作日志独立 Tab 状态 */
            const logList = ref([]);
            const logLoading = ref(false);
            const logPage = ref(1);
            const logTotal = ref(0);
            const logLimit = ref(10);
            const logDateRange = ref([]);
            const logOperator = ref('');
            const logKeyword = ref('');

            /* 从 URL 参数获取农户 ID */
            const getFarmerId = () => {
                const params = new URLSearchParams(window.location.search);
                return parseInt(params.get('id') || '0', 10);
            };

            /* 加载详情数据 */
            const fetchDetail = () => {
                const id = getFarmerId();
                if (!id) return;
                loading.value = true;
                request.get('/farmer/' + id).then((res) => {
                    const data = res.data.data || res.data;
                    detail.value = data;
                    switcherId.value = data.id;
                    /* 填充可编辑表单 */
                    Object.assign(editForm, {
                        nickname: data.nickname || '',
                        avatar: data.avatar || '',
                        phone: data.phone || '',
                        email: data.email || '',
                        company: data.company || '',
                        address: data.address || '',
                        remark: data.remark || '',
                        country: data.country || '中国',
                        language: data.language || '中文简体',
                        password: ''
                    });
                }).catch(() => {
                    MessagePlugin.error('加载农户详情失败');
                }).finally(() => {
                    loading.value = false;
                });
            };

            /* 加载操作日志（分页） */
            const fetchLogs = () => {
                var id = getFarmerId();
                if (!id) return;
                logLoading.value = true;
                var params = {
                    page: logPage.value,
                    limit: logLimit.value,
                    keyword: logKeyword.value,
                    operator: logOperator.value
                };
                /* 日期范围 */
                if (logDateRange.value && logDateRange.value.length === 2) {
                    params.date_from = logDateRange.value[0];
                    params.date_to = logDateRange.value[1];
                }
                request.get('/farmer/' + id + '/logs', { params: params }).then((res) => {
                    logList.value = res.data.data.list || [];
                    logTotal.value = res.data.data.total || 0;
                }).catch(() => {}).finally(() => {
                    logLoading.value = false;
                });
            };

            /* 分页变更 */
            const onLogPageChange = (pageInfo) => {
                logPage.value = pageInfo.current;
                fetchLogs();
            };

            /* 加载轻量用户列表（用于快捷切换下拉） */
            const fetchFarmerList = () => {
                request.get('/farmer/simple-list').then((res) => {
                    farmerList.value = res.data.data.list || [];
                }).catch(() => {});
            };

            /* 用户切换 */
            const onSwitcherChange = (val) => {
                if (!val || val === getFarmerId()) return;
                var url = '/admin/farmer-detail?id=' + val;
                if (window.HuiYan && window.HuiYan.loadPage) {
                    window.HuiYan.loadPage(url);
                } else {
                    window.location.href = url;
                }
            };

            /* 保存 */
            const doSave = () => {
                if (!detail.value) return;
                saving.value = true;
                var payload = {
                    nickname: editForm.nickname,
                    avatar: editForm.avatar,
                    phone: editForm.phone,
                    email: editForm.email,
                    company: editForm.company,
                    address: editForm.address,
                    remark: editForm.remark,
                    country: editForm.country,
                    language: editForm.language
                };
                if (editForm.password) {
                    payload.password = editForm.password;
                }
                request.put('/farmer/' + detail.value.id, payload).then((res) => {
                    MessagePlugin.success(res.data.msg || '保存成功');
                    fetchDetail();
                }).catch(() => {}).finally(() => {
                    saving.value = false;
                });
            };

            /* 删除 */
            const doDelete = () => {
                if (!detail.value) return;
                var instance = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '确定要删除农户 [' + detail.value.username + '] 吗？此操作不可恢复。',
                    theme: 'danger',
                    onConfirm: () => {
                        request.delete('/farmer/' + detail.value.id).then(() => {
                            MessagePlugin.success('已删除');
                            instance.destroy();
                            goBack();
                        }).catch(() => { instance.destroy(); });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            /* 停用/启用 */
            const doToggleStatus = () => {
                if (!detail.value) return;
                var newStatus = detail.value.status === 1 ? 0 : 1;
                var actionText = newStatus === 1 ? '启用' : '停用';
                var instance = DialogPlugin.confirm({
                    header: '确认' + actionText,
                    body: '确定要' + actionText + '农户 [' + detail.value.username + '] 吗？',
                    onConfirm: () => {
                        request.put('/farmer/' + detail.value.id + '/status', { status: newStatus }).then(() => {
                            MessagePlugin.success(actionText + '成功');
                            instance.destroy();
                            fetchDetail();
                        }).catch(() => { instance.destroy(); });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            /* 管理员快捷登录农户账号 */
            const doQuickLogin = () => {
                if (!detail.value) return;
                var instance = DialogPlugin.confirm({
                    header: '快捷登录',
                    body: '将以农户 [' + detail.value.username + '] 的身份登录农户端，是否继续？',
                    onConfirm: () => {
                        request.post('/farmer/' + detail.value.id + '/impersonate').then((res) => {
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

            /* 返回列表 */
            const goBack = () => {
                if (window.HuiYan && window.HuiYan.loadPage) {
                    window.HuiYan.loadPage('/admin/farmer');
                } else {
                    window.location.href = '/admin/farmer';
                }
            };

            /* 获取实名认证状态 */
            const fetchCertStatus = () => {
                var id = getFarmerId();
                if (!id) return;
                request.get('/certification/record/farmer/' + id).then((res) => {
                    var data = res.data.data || res.data;
                    if (data && data.length > 0) {
                        certStatus.value = data[0].status;
                    } else {
                        certStatus.value = null;
                    }
                }).catch(() => {
                    certStatus.value = null;
                });
            };

            /* 头像上传 */
            const uploadAvatar = (file) => {
                return new Promise((resolve) => {
                    var formDataObj = new FormData();
                    formDataObj.append('file', file.raw);
                    request.post('/upload/image', formDataObj).then((res) => {
                        if (res.data.status === 200 && res.data.data) {
                            editForm.avatar = res.data.data.url;
                            MessagePlugin.success('头像上传成功');
                        }
                        resolve({ status: res.data.status === 200 ? 'success' : 'fail' });
                    }).catch(() => {
                        resolve({ status: 'fail' });
                    });
                });
            };

            onMounted(() => {
                fetchDetail();
                fetchFarmerList();
                fetchCertStatus();
            });

            /* 切换到操作日志 Tab 时自动加载日志（immediate 兼容 URL 直达） */
            watch(activeTab, (val) => {
                if (val === 'login' && logList.value.length === 0) {
                    fetchLogs();
                }
            }, { immediate: true });

            return {
                loading, saving, detail, activeTab, editForm, certStatus, avatarFileList,
                farmerList, switcherId, searchField,
                opLogColumns, shortLogColumns,
                logList, logLoading, logPage, logTotal, logLimit,
                logDateRange, logOperator, logKeyword,
                fetchLogs, onLogPageChange, onSwitcherChange, uploadAvatar,
                doSave, doDelete, doToggleStatus, doQuickLogin, goBack
            };
        }
    });
})();
