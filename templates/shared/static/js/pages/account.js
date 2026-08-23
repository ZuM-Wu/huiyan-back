/**
 * 账户设置页面脚本
 * Tab 1: 基本信息（昵称/邮箱/手机号）
 * Tab 2: 修改密码
 * Tab 3: API 密钥（MCP 接入，创建/列表/吊销，明文可视化，列表可随时查看与复制）
 *
 * 对应路由: /admin/account
 * 依赖: Vue 3 + TDesign + HuiYan.createPage + request (axios)
 */
(function () {
    'use strict';

    const { ref, reactive, watch, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            // ========== 公共状态 ==========
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('info', ['info', 'password', 'apikey']));
            watch(activeTab, (val) => { HuiYan.syncUrlTab(val); });
            const savingInfo = ref(false);
            const savingPwd = ref(false);

            // ========== Tab 1: 基本信息 ==========
            const infoForm = reactive({
                username: '',
                nickname: '',
                email: '',
                phone: '',
                last_login_ip: '',
                create_time: ''
            });

            // ========== Tab 2: 修改密码 ==========
            const pwdFormRef = ref(null);
            const pwdForm = reactive({
                old_password: '',
                new_password: '',
                confirm_password: ''
            });
            // 校验规则：未填写/不合规时内联显示在输入框下方（见视觉规范 9.9）
            const pwdRules = {
                old_password: [{ required: true, message: '请输入当前密码', type: 'error' }],
                new_password: [
                    { required: true, message: '请输入新密码', type: 'error' },
                    { min: 6, message: '新密码至少 6 位', type: 'error' }
                ],
                confirm_password: [
                    { required: true, message: '请再次输入新密码', type: 'error' },
                    { validator: (val) => val === pwdForm.new_password, message: '两次输入的新密码不一致', type: 'error' }
                ]
            };

            // ========== 数据获取 ==========
            const fetchProfile = () => {
                return request.get('/profile').then(res => {
                    const data = res.data || res;
                    const info = data.data || {};
                    infoForm.username = info.username || '';
                    infoForm.nickname = info.nickname || '';
                    infoForm.email = info.email || '';
                    infoForm.phone = info.phone || '';
                    infoForm.last_login_ip = info.last_login_ip || '';
                    infoForm.create_time = info.create_time || '';
                }).catch(err => {
                    console.error('[账户设置] 信息加载失败:', err);
                    MessagePlugin.error('信息加载失败');
                });
            };

            // ========== 保存基本信息 ==========
            const saveInfo = () => {
                savingInfo.value = true;
                request.put('/profile', {
                    nickname: infoForm.nickname,
                    email: infoForm.email,
                    phone: infoForm.phone
                }).then(res => {
                    const data = res.data || res;
                    MessagePlugin.success(data.msg || '保存成功');
                    // 同步更新侧边栏用户名
                    const cached = JSON.parse(localStorage.getItem('admin_user') || '{}');
                    cached.nickname = infoForm.nickname;
                    localStorage.setItem('admin_user', JSON.stringify(cached));
                }).catch(err => {
                    const msg = (err.response && err.response.data && err.response.data.detail) || '保存失败';
                    MessagePlugin.error(msg);
                }).finally(() => {
                    savingInfo.value = false;
                });
            };

            // ========== 修改密码 ==========
            const savePassword = (params) => {
                // TDesign 表单校验：未通过时错误已内联显示在输入框下方，直接中断
                if (params && params.validateResult !== true) return;

                savingPwd.value = true;
                request.put('/profile/password', {
                    old_password: pwdForm.old_password,
                    new_password: pwdForm.new_password
                }).then(res => {
                    const data = res.data || res;
                    MessagePlugin.success(data.msg || '密码修改成功');
                    // 清空表单与校验状态
                    if (pwdFormRef.value) pwdFormRef.value.reset();
                    // 修改密码后退出登录
                    setTimeout(() => {
                        localStorage.removeItem('admin_token');
                        localStorage.removeItem('admin_user');
                        localStorage.removeItem('admin_menus');
                        localStorage.removeItem('admin_auth');
                        localStorage.removeItem('admin_pages');
                        window.location.href = '/admin/login';
                    }, 1500);
                }).catch(err => {
                    const msg = (err.response && err.response.data && err.response.data.detail) || '密码修改失败';
                    MessagePlugin.error(msg);
                }).finally(() => {
                    savingPwd.value = false;
                });
            };

            // ========== Tab 3: API 密钥（MCP 接入） ==========
            const apiKeys = ref([]);
            const loadingKeys = ref(false);
            const createKeyVisible = ref(false);
            const creatingKey = ref(false);
            const keyForm = reactive({ name: '' });
            const keyNameError = ref('');
            const keyResultVisible = ref(false);
            // 结果弹窗标题：创建成功/行内详情复用同一弹窗，仅标题不同
            const keyResultTitle = ref('密钥创建成功');
            const newKeyPlain = ref('');
            const newKeyMcpJson = ref('');
            const apiKeyColumns = [
                { colKey: 'key', title: '密钥', width: 460 },
                { colKey: 'name', title: '备注名' },
                { colKey: 'status', title: '状态', width: 100 },
                { colKey: 'last_used_time', title: '最后使用时间', width: 180 },
                { colKey: 'create_time', title: '创建时间', width: 180 },
                { colKey: 'op', title: '操作', width: 140 }
            ];

            const fetchApiKeys = () => {
                loadingKeys.value = true;
                request.get('/api-key/list').then(res => {
                    const data = res.data || res;
                    apiKeys.value = (data.data && data.data.list) || [];
                }).catch(() => {
                    MessagePlugin.error('密钥列表加载失败');
                }).finally(() => {
                    loadingKeys.value = false;
                });
            };

            const openCreateKey = () => {
                keyForm.name = '';
                keyNameError.value = '';
                createKeyVisible.value = true;
            };

            // 拼 MCP 客户端 mcpServers 配置片段（明文 Key 已填入，降低接入门槛）
            const buildMcpJson = (plainKey) => JSON.stringify({
                mcpServers: {
                    huiyan: {
                        url: window.location.origin + '/mcp',
                        headers: { Authorization: 'Bearer ' + plainKey }
                    }
                }
            }, null, 2);

            const createKey = () => {
                // 内联校验：备注名必填
                if (!keyForm.name.trim()) {
                    keyNameError.value = '请输入密钥备注名';
                    return;
                }
                creatingKey.value = true;
                request.post('/api-key/create', { name: keyForm.name.trim() }).then(res => {
                    const data = res.data || res;
                    newKeyPlain.value = (data.data && data.data.key) || '';
                    newKeyMcpJson.value = buildMcpJson(newKeyPlain.value);
                    createKeyVisible.value = false;
                    keyResultTitle.value = '密钥创建成功';
                    keyResultVisible.value = true;
                    fetchApiKeys();
                }).catch(err => {
                    const msg = (err.response && err.response.data && err.response.data.detail) || '创建失败';
                    MessagePlugin.error(msg);
                }).finally(() => {
                    creatingKey.value = false;
                });
            };

            // 行内详情：复用创建成功弹窗展示密钥明文与 MCP 接入配置（存量无明文密钥不提供详情入口）
            const openKeyDetail = (row) => {
                newKeyPlain.value = row.key;
                newKeyMcpJson.value = buildMcpJson(row.key);
                keyResultTitle.value = '密钥详情';
                keyResultVisible.value = true;
            };

            const revokeKey = (row) => {
                request.post('/api-key/revoke', { id: row.id }).then(res => {
                    const data = res.data || res;
                    MessagePlugin.success(data.msg || '吊销成功');
                    fetchApiKeys();
                }).catch(err => {
                    const msg = (err.response && err.response.data && err.response.data.detail) || '吊销失败';
                    MessagePlugin.error(msg);
                });
            };

            const copyText = (text, label) => {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(() => {
                        MessagePlugin.success(label + '已复制');
                    }).catch(() => MessagePlugin.error('复制失败，请手动选择复制'));
                } else {
                    // 非安全上下文（http）降级：选中提示手动复制
                    MessagePlugin.warning('当前环境不支持一键复制，请手动选择复制');
                }
            };

            // ========== 初始化 ==========
            onMounted(() => {
                fetchProfile();
                fetchApiKeys();
            });

            return {
                activeTab, savingInfo, savingPwd,
                infoForm, pwdForm, pwdFormRef, pwdRules,
                saveInfo, savePassword,
                apiKeys, loadingKeys, apiKeyColumns,
                createKeyVisible, creatingKey, keyForm, keyNameError,
                keyResultVisible, keyResultTitle, newKeyPlain, newKeyMcpJson,
                openCreateKey, createKey, openKeyDetail, revokeKey, copyText
            };
        }
    });
})();
