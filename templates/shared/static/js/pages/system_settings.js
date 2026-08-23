/**
 * 系统设置页面脚本 — 5 Tab 合并页面
 * 包含：基本设置、访问设置、系统缓存、上传设置、存储设置
 *
 * 对应路由: /admin/system
 * 依赖: Vue 3 + TDesign + HuiYan.createPage + request (axios)
 */
(function () {
    'use strict';

    const { ref, reactive, onMounted, watch, computed } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            // ========== 公共状态 ==========
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('basic', ['basic', 'access', 'cache', 'upload', 'storage']));
            watch(activeTab, (val) => { HuiYan.syncUrlTab(val); });
            // 访问设置内部子 Tab：前台设置 / 后台设置
            const accessSubTab = ref('frontend');
            const saving = ref(false);

            // ========== Tab 1: 基本设置 ==========
            const basicForm = reactive({
                site_name: '', site_subtitle: '', site_domain: '',
                site_logo: '', site_favicon: '', login_bg: '',
                site_logo_url: '', site_logo_target: '_self',
                site_maintenance: 0, maintenance_message: '',
                start_id_admin: 1, start_id_farmer: 1,
                page_size_default: 20, page_size_max: 100,
                record_number: '', copyright: ''
            });

            // ========== Tab 2: 访问设置 - 后台设置 ==========
            const accessForm = reactive({
                login_session_duration: '7200',
                login_retry_limit: 5,
                login_lock_duration: '900',
                session_concurrent: 1,
                force_password_complexity: 1,
                admin_enforce_safe: 0,
                ip_whitelist_enabled: 0,
                ip_whitelist: ''
            });

            // ========== Tab 2: 访问设置 - 前台设置（农户） ==========
            const frontendForm = reactive({
                allow_farmer_register: 1,
                farmer_email_suffix_enabled: 0,
                farmer_email_suffixes: '',
                farmer_session_duration: '7200',
                farmer_register_phone_required: 0,
                farmer_register_email_required: 0,
                farmer_service_agreement_url: '',
                farmer_privacy_policy_url: '',
                // 验证码登录 / 注册方式控制
                farmer_allow_phone_register: 1,
                farmer_phone_register_verify: 1,
                farmer_phone_password_login: 1,
                farmer_phone_sms_login: 1,
                farmer_allow_email_register: 1,
                farmer_email_register_verify: 1,
                farmer_email_password_login: 1,
                farmer_email_code_login: 1,
                farmer_show_register_switch: 1,
                farmer_default_login_method: 'password',
                farmer_default_password_type: 'phone'
            });

            // 会话时长选项
            const sessionOptions = [
                { label: '1 小时', value: '3600' },
                { label: '2 小时', value: '7200' },
                { label: '4 小时', value: '14400' },
                { label: '24 小时', value: '86400' }
            ];

            // 农户端允许更长的登录有效期，管理员会话仍使用上面的短时选项
            const farmerSessionOptions = [
                { label: '1 小时', value: '3600' },
                { label: '2 小时', value: '7200' },
                { label: '4 小时', value: '14400' },
                { label: '1 天', value: '86400' },
                { label: '3 天', value: '259200' },
                { label: '5 天', value: '432000' },
                { label: '7 天', value: '604800' }
            ];

            // 登录方式选项
            const loginMethodOptions = [
                { label: '密码登录', value: 'password' },
                { label: '验证码登录', value: 'code' }
            ];

            // 密码登录凭证选项
            const passwordTypeOptions = [
                { label: '手机号', value: 'phone' },
                { label: '电子邮箱', value: 'email' }
            ];

            // 锁定时间选项
            const lockOptions = [
                { label: '5 分钟', value: '300' },
                { label: '15 分钟', value: '900' },
                { label: '30 分钟', value: '1800' },
                { label: '1 小时', value: '3600' },
                { label: '永久锁定', value: '0' }
            ];

            // ========== Tab 3: 系统缓存 ==========
            const clearing = ref('');
            const cacheActions = [
                { key: 'all', title: '全部缓存', desc: '清除系统所有缓存数据', endpoint: '/cache/clear_all', theme: 'danger' },
                { key: 'plugin', title: '插件缓存', desc: '清除插件列表与钩子缓存', endpoint: '/cache/clear_plugin', theme: 'primary' },
                { key: 'config', title: '配置缓存', desc: '清除系统配置项缓存', endpoint: '/cache/clear_config', theme: 'primary' },
                { key: 'permission', title: '权限/菜单缓存', desc: '清除权限节点与菜单树缓存', endpoint: '/cache/clear_permission', theme: 'primary' }
            ];

            // ========== Tab 4: 存储设置 ==========
            const storageLoading = ref(false);
            const storageList = ref([]);
            const currentStorageMethod = ref('');
            const storageMethod = ref('');
            const testingConnection = ref(false);
            const switchDialogVisible = ref(false);
            const switchTargetMethod = ref('');
            const switchTargetName = ref('');
            const switchPassword = ref('');
            const switching = ref(false);
            const storageColumns = [
                { colKey: 'title', title: '插件名称', minWidth: 120 },
                { colKey: 'author', title: '开发者', minWidth: 100 },
                { colKey: 'version', title: '版本', width: 100 },
                { colKey: 'status', title: '状态', width: 100, cell: 'status' },
                { colKey: 'has_data', title: '是否存在数据', width: 120, cell: 'has_data' },
                { colKey: 'is_current', title: '当前使用', width: 100, cell: 'is_current' },
                { colKey: 'op', title: '操作', width: 120, cell: 'op', fixed: 'right' },
            ];
            const storageMethodOptions = computed(() => {
                return storageList.value
                    .filter(item => item.status === 1)
                    .map(item => ({ label: item.title, value: item.name }));
            });
            const currentStorageMethodLabel = computed(() => {
                const found = storageList.value.find(item => item.name === currentStorageMethod.value);
                return found ? found.title : currentStorageMethod.value;
            });

            // ========== 数据获取 ==========

            /**
             * 加载全部配置，分发到 basicForm 和 accessForm
             */
            const fetchConfig = () => {
                return request.get('/config/list').then(res => {
                    const data = res.data.data || res.data;
                    const map = {};
                    (data.list || []).forEach(item => { map[item.key] = item.value; });

                    // 基本设置映射
                    const basicKeys = [
                        'site_name', 'site_subtitle', 'site_domain', 'site_logo', 'site_favicon',
                        'login_bg', 'site_logo_url', 'maintenance_message', 'record_number', 'copyright'
                    ];
                    basicKeys.forEach(k => {
                        if (map[k] !== undefined) basicForm[k] = map[k];
                    });
                    // Logo 跳转方式（默认当前页面打开 _self）
                    basicForm.site_logo_target = map.site_logo_target || '_self';
                    // 布尔值字段（开关项用 1/0 数值）
                    basicForm.site_maintenance = parseInt(map.site_maintenance) || 0;
                    // 数值字段
                    basicForm.start_id_admin = parseInt(map.start_id_admin) || 1;
                    basicForm.start_id_farmer = parseInt(map.start_id_farmer) || 1;
                    basicForm.page_size_default = parseInt(map.page_size_default) || 20;
                    basicForm.page_size_max = parseInt(map.page_size_max) || 100;

                    // 访问设置映射
                    accessForm.login_session_duration = map.login_session_duration || '7200';
                    accessForm.login_retry_limit = parseInt(map.login_retry_limit) || 5;
                    accessForm.login_lock_duration = map.login_lock_duration || '900';
                    accessForm.session_concurrent = parseInt(map.session_concurrent) || 0;
                    accessForm.force_password_complexity = parseInt(map.force_password_complexity) || 0;
                    accessForm.admin_enforce_safe = parseInt(map.admin_enforce_safe) || 0;
                    accessForm.ip_whitelist_enabled = parseInt(map.ip_whitelist_enabled) || 0;
                    accessForm.ip_whitelist = map.ip_whitelist || '';

                    // 前台（农户）访问设置映射
                    frontendForm.allow_farmer_register = parseInt(map.allow_farmer_register);
                    if (isNaN(frontendForm.allow_farmer_register)) frontendForm.allow_farmer_register = 1;
                    frontendForm.farmer_email_suffix_enabled = parseInt(map.farmer_email_suffix_enabled) || 0;
                    frontendForm.farmer_email_suffixes = map.farmer_email_suffixes || '';
                    frontendForm.farmer_session_duration = map.farmer_session_duration || '7200';
                    frontendForm.farmer_register_phone_required = parseInt(map.farmer_register_phone_required) || 0;
                    frontendForm.farmer_register_email_required = parseInt(map.farmer_register_email_required) || 0;
                    frontendForm.farmer_service_agreement_url = map.farmer_service_agreement_url || '';
                    frontendForm.farmer_privacy_policy_url = map.farmer_privacy_policy_url || '';
                    // 验证码登录 / 注册方式控制
                    frontendForm.farmer_allow_phone_register = parseInt(map.farmer_allow_phone_register);
                    if (isNaN(frontendForm.farmer_allow_phone_register)) frontendForm.farmer_allow_phone_register = 1;
                    frontendForm.farmer_phone_register_verify = parseInt(map.farmer_phone_register_verify);
                    if (isNaN(frontendForm.farmer_phone_register_verify)) frontendForm.farmer_phone_register_verify = 1;
                    frontendForm.farmer_phone_password_login = parseInt(map.farmer_phone_password_login);
                    if (isNaN(frontendForm.farmer_phone_password_login)) frontendForm.farmer_phone_password_login = 1;
                    frontendForm.farmer_phone_sms_login = parseInt(map.farmer_phone_sms_login);
                    if (isNaN(frontendForm.farmer_phone_sms_login)) frontendForm.farmer_phone_sms_login = 1;
                    frontendForm.farmer_allow_email_register = parseInt(map.farmer_allow_email_register);
                    if (isNaN(frontendForm.farmer_allow_email_register)) frontendForm.farmer_allow_email_register = 1;
                    frontendForm.farmer_email_register_verify = parseInt(map.farmer_email_register_verify);
                    if (isNaN(frontendForm.farmer_email_register_verify)) frontendForm.farmer_email_register_verify = 1;
                    frontendForm.farmer_email_password_login = parseInt(map.farmer_email_password_login);
                    if (isNaN(frontendForm.farmer_email_password_login)) frontendForm.farmer_email_password_login = 1;
                    frontendForm.farmer_email_code_login = parseInt(map.farmer_email_code_login);
                    if (isNaN(frontendForm.farmer_email_code_login)) frontendForm.farmer_email_code_login = 1;
                    frontendForm.farmer_show_register_switch = parseInt(map.farmer_show_register_switch);
                    if (isNaN(frontendForm.farmer_show_register_switch)) frontendForm.farmer_show_register_switch = 1;
                    frontendForm.farmer_default_login_method = map.farmer_default_login_method || 'password';
                    frontendForm.farmer_default_password_type = map.farmer_default_password_type || 'phone';
                }).catch(err => {
                    console.error('[系统设置] 配置加载失败:', err);
                    // 错误提示由 request.js 拦截器统一透传后端 msg
                });
            };

            // ========== 数据保存 ==========

            /**
             * 保存基本设置
             */
            const saveBasic = () => {
                saving.value = true;
                const payload = {
                    site_name: basicForm.site_name,
                    site_subtitle: basicForm.site_subtitle,
                    site_domain: basicForm.site_domain,
                    site_logo: basicForm.site_logo,
                    site_favicon: basicForm.site_favicon,
                    login_bg: basicForm.login_bg,
                    site_logo_url: basicForm.site_logo_url,
                    site_logo_target: basicForm.site_logo_target,
                    site_maintenance: String(basicForm.site_maintenance),
                    maintenance_message: basicForm.maintenance_message,
                    start_id_admin: String(basicForm.start_id_admin),
                    start_id_farmer: String(basicForm.start_id_farmer),
                    page_size_default: String(basicForm.page_size_default),
                    page_size_max: String(basicForm.page_size_max),
                    record_number: basicForm.record_number,
                    copyright: basicForm.copyright
                };
                request.put('/config/batch', payload).then(() => {
                    MessagePlugin.success('基本设置已保存');
                    localStorage.removeItem('admin_menus');
                }).catch(() => {
                    /* 错误提示由 request.js 拦截器统一透传 */
                }).finally(() => { saving.value = false; });
            };

            /**
             * 保存前台（农户）访问设置
             */
            const saveFrontend = () => {
                saving.value = true;
                const payload = {
                    allow_farmer_register: String(frontendForm.allow_farmer_register),
                    farmer_email_suffix_enabled: String(frontendForm.farmer_email_suffix_enabled),
                    farmer_email_suffixes: frontendForm.farmer_email_suffixes,
                    farmer_session_duration: frontendForm.farmer_session_duration,
                    farmer_register_phone_required: String(frontendForm.farmer_register_phone_required),
                    farmer_register_email_required: String(frontendForm.farmer_register_email_required),
                    farmer_service_agreement_url: frontendForm.farmer_service_agreement_url,
                    farmer_privacy_policy_url: frontendForm.farmer_privacy_policy_url,
                    // 验证码登录 / 注册方式控制
                    farmer_allow_phone_register: String(frontendForm.farmer_allow_phone_register),
                    farmer_phone_register_verify: String(frontendForm.farmer_phone_register_verify),
                    farmer_phone_password_login: String(frontendForm.farmer_phone_password_login),
                    farmer_phone_sms_login: String(frontendForm.farmer_phone_sms_login),
                    farmer_allow_email_register: String(frontendForm.farmer_allow_email_register),
                    farmer_email_register_verify: String(frontendForm.farmer_email_register_verify),
                    farmer_email_password_login: String(frontendForm.farmer_email_password_login),
                    farmer_email_code_login: String(frontendForm.farmer_email_code_login),
                    farmer_show_register_switch: String(frontendForm.farmer_show_register_switch),
                    farmer_default_login_method: frontendForm.farmer_default_login_method,
                    farmer_default_password_type: frontendForm.farmer_default_password_type
                };
                request.put('/config/batch', payload).then(() => {
                    MessagePlugin.success('前台设置已保存');
                }).catch(() => {
                    /* 错误提示由 request.js 拦截器统一透传 */
                }).finally(() => { saving.value = false; });
            };

            /**
             * 保存访问设置
             */
            const saveAccess = () => {
                saving.value = true;
                const payload = {
                    login_session_duration: accessForm.login_session_duration,
                    login_retry_limit: String(accessForm.login_retry_limit),
                    login_lock_duration: accessForm.login_lock_duration,
                    session_concurrent: String(accessForm.session_concurrent),
                    force_password_complexity: String(accessForm.force_password_complexity),
                    admin_enforce_safe: String(accessForm.admin_enforce_safe),
                    ip_whitelist_enabled: String(accessForm.ip_whitelist_enabled),
                    ip_whitelist: accessForm.ip_whitelist
                };
                request.put('/config/batch', payload).then(() => {
                    MessagePlugin.success('后台设置已保存');
                }).catch(() => {
                    /* 错误提示由 request.js 拦截器统一透传 */
                }).finally(() => { saving.value = false; });
            };

            /**
             * 清除缓存（带确认对话框）
             */
            const doClear = (item) => {
                const instance = DialogPlugin.confirm({
                    header: '确认清除',
                    body: '确定要清除【' + item.title + '】吗？',
                    onConfirm: () => {
                        clearing.value = item.key;
                        request.post(item.endpoint).then(() => {
                            MessagePlugin.success(item.title + '已清除');
                            instance.destroy();
                            localStorage.removeItem('admin_menus');
                        }).catch(() => {
                            /* 错误提示由 request.js 拦截器统一透传 */
                        }).finally(() => { clearing.value = ''; });
                    }
                });
            };

            // ========== 初始化 ==========
            onMounted(() => {
                fetchConfig();
                fetchStorageList();
            });

            // ========== Tab 4: 存储设置方法 ==========
            const fetchStorageList = () => {
                storageLoading.value = true;
                return request.get('/oss/list').then(res => {
                    // OSS 接口遵循统一响应信封，列表载荷位于 response.data.data
                    const data = (res.data && res.data.data) || res.data || res;
                    currentStorageMethod.value = data.current_method || 'local_oss';
                    storageMethod.value = currentStorageMethod.value;
                    storageList.value = (data.list || []).map(item => ({
                        ...item,
                        is_current: item.name === currentStorageMethod.value
                    }));
                }).catch(() => {}).finally(() => { storageLoading.value = false; });
            };

            const testConnection = () => {
                testingConnection.value = true;
                request.post('/oss/test').then(res => {
                    const data = (res.data && res.data.data) || res.data || res;
                    if (data.status === 200 || data.success === true) {
                        MessagePlugin.success('连接成功：' + (data.msg || data.message || ''));
                    } else {
                        MessagePlugin.error('连接失败：' + (data.msg || data.message || '未知错误'));
                    }
                }).catch(() => {}).finally(() => { testingConnection.value = false; });
            };

            const openSwitchDialog = (row) => {
                switchTargetMethod.value = row.name;
                switchTargetName.value = row.title;
                switchPassword.value = '';
                switchDialogVisible.value = true;
            };

            const openSwitchDialogFromSelect = () => {
                if (storageMethod.value === currentStorageMethod.value) {
                    MessagePlugin.info('存储方式未变更');
                    return;
                }
                const target = storageList.value.find(item => item.name === storageMethod.value);
                switchTargetMethod.value = storageMethod.value;
                switchTargetName.value = target ? target.title : storageMethod.value;
                switchPassword.value = '';
                switchDialogVisible.value = true;
            };

            const confirmSwitch = () => {
                if (!switchPassword.value) {
                    MessagePlugin.warning('请输入管理员密码');
                    return;
                }
                switching.value = true;
                request.put('/oss/switch', {
                    oss_method: switchTargetMethod.value,
                    password: switchPassword.value
                }).then(() => {
                    MessagePlugin.success('存储方式已切换');
                    switchDialogVisible.value = false;
                    fetchStorageList();
                }).catch(() => {}).finally(() => { switching.value = false; });
            };

            return {
                activeTab, accessSubTab, saving,
                // Tab 1
                basicForm, saveBasic,
                // Tab 2 - 后台设置
                accessForm, sessionOptions, lockOptions, saveAccess,
                // Tab 2 - 前台设置
                frontendForm, farmerSessionOptions, saveFrontend,
                loginMethodOptions, passwordTypeOptions,
                // Tab 3
                clearing, cacheActions, doClear,
                // Tab 5 - 存储设置
                storageLoading, storageList, currentStorageMethodLabel,
                storageMethod, storageMethodOptions,
                storageColumns, testingConnection, testConnection,
                switchDialogVisible, switchTargetName, switchPassword,
                switching, openSwitchDialog, openSwitchDialogFromSelect, confirmSwitch
            };
        }
    });
})();
