/**
 * 登录页脚本
 * - 登录逻辑（用户名/密码 → JWT）
 * - 站点配置加载（Logo + 名称，与侧边栏一致）
 */
(function () {
    var { createApp, ref, computed, onMounted, reactive } = Vue;
    var { MessagePlugin } = TDesign;

    var pageOptions = {
        setup: function () {
            // ========== 登录表单状态 ==========
            var loading = ref(false);
            // 表单数据（供 TDesign Form 校验使用，错误直接内联显示在输入框下方）
            var loginForm = reactive({ username: '', password: '' });
            // 校验规则：未填写时在对应输入框下方显示红色提示（不再弹出消息）
            var rules = {
                username: [{ required: true, message: '请输入用户名/手机号/邮箱', type: 'error' }],
                password: [{ required: true, message: '请输入密码', type: 'error' }]
            };

            // ========== 站点配置（Logo + 背景图） ==========
            var siteLogo = ref('/static/img/logo.png');
            var loginBg = ref('');

            /**
             * 背景图样式（有配置时显示图片，否则透明）
             */
            var bgStyle = computed(function () {
                if (loginBg.value) {
                    return { backgroundImage: 'url(' + loginBg.value + ')' };
                }
                return {};
            });

            /**
             * 加载站点配置（Logo + 登录背景图 + Favicon）
             * 登录页处于未登录态，必须调用公开端点 /config/site（无需鉴权），
             * 不能用需要管理员鉴权的 /config/list（否则 401，背景图/Logo 无法生效）
             */
            var loadSiteConfig = function () {
                axios.get('/api/admin/v1/config/site').then(function (res) {
                    var data = res.data.data || res.data;
                    var list = data.list || [];
                    var map = {};
                    list.forEach(function (item) { map[item.key] = item.value; });
                    if (map.site_logo) { siteLogo.value = map.site_logo; }
                    if (map.login_bg) { loginBg.value = map.login_bg; }
                    // 动态设置浏览器标签页 Favicon（base.html 中为默认占位）
                    if (map.site_favicon) {
                        var favEl = document.querySelector("link[rel='icon']");
                        if (favEl) { favEl.href = map.site_favicon; }
                    }
                }).catch(function () { /* 静默失败，使用默认值 */ });
            };

            // 预拉当前管理员的权限与可访问页面映射，写入 localStorage（供 v-permission 与页面守卫使用）
            var prefetchAuth = function (token) {
                return axios.get('/api/admin/v1/permission/my-auth', {
                    headers: { Authorization: 'Bearer ' + token }
                }).then(function (res) {
                    var d = (res.data && res.data.data) || res.data || {};
                    localStorage.setItem('admin_auth', JSON.stringify(d.auth || []));
                    localStorage.setItem('admin_pages', JSON.stringify(d.pages || {}));
                }).catch(function () { /* 静默失败，守卫会在缓存缺失时自行补拉 */ });
            };

            // ========== 登录提交 ==========
            var onLogin = function (params) {
                // 阻止表单默认提交行为（页面跳转）
                if (params && params.e) params.e.preventDefault();
                // TDesign 表单校验未通过时（validateResult 非 true），错误已内联显示，直接中断
                if (params && params.validateResult !== true) { return; }
                loading.value = true;
                axios.post('/api/admin/v1/login', {
                    username: loginForm.username,
                    password: loginForm.password
                }).then(function (res) {
                    var data = res.data;
                    if (data.token) {
                        localStorage.setItem('admin_token', data.token);
                        localStorage.setItem('admin_user', JSON.stringify({
                            nickname: loginForm.username,
                            username: loginForm.username
                        }));
                        localStorage.removeItem('admin_menus');
                        localStorage.removeItem('admin_auth');
                        localStorage.removeItem('admin_pages');
                        MessagePlugin.success('登录成功');
                        // 登录成功后预拉权限与可访问页面映射，写入缓存供侧边栏/页面守卫使用
                        // 无论成败都跳转（守卫在缓存缺失时会自行补拉）
                        prefetchAuth(data.token).finally(function () {
                            setTimeout(function () {
                                window.location.href = '/admin/dashboard';
                            }, 300);
                        });
                    } else {
                        MessagePlugin.error(data.msg || data.detail || '登录失败');
                    }
                }).catch(function (err) {
                    var msg = '登录失败';
                    if (err.response && err.response.data) {
                        msg = err.response.data.detail || err.response.data.msg || msg;
                    }
                    MessagePlugin.error(msg);
                }).finally(function () {
                    loading.value = false;
                });
            };

            // ========== 初始化 ==========
            onMounted(function () {
                loadSiteConfig();
            });

            return { loading, loginForm, rules, onLogin, siteLogo, bgStyle };
        }
    };

    // 复用统一页面引导，确保登录页也使用本地图标桥接和 TDesign 初始化约定。
    if (window.HuiYan && window.HuiYan.createPage) {
        window.HuiYan.createPage(pageOptions, '#login-app');
    } else {
        // 兼容未加载 hy-app.js 的历史壳页面。
        var app = createApp(pageOptions);
        app.config.compilerOptions.delimiters = ['[[', ']]'];
        app.use(TDesign);
        app.mount('#login-app');
    }
})();
