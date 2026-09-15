/**
 * 农户端引导脚本 — 侧边栏 + 顶栏 + 页面 Vue 实例初始化
 *
 * 架构对齐管理端 layout.js：三个独立 Vue 实例
 *   #sidebar-app — 侧边栏菜单 + 用户卡片
 *   #farmer-topbar  — 顶部栏折叠按钮
 *   #farmer-app     — 页面内容（由 HuiYanFarmer.createApp 挂载）
 *
 * 用法（页面脚本）：
 *   HuiYanFarmer.createApp({
 *       setup() {
 *           var ref = Vue.ref;
 *           // ... 页面逻辑
 *           return { ... };
 *       }
 *   });
 */
(function (window) {
    'use strict';

    // 品牌配置由服务端在首屏从数据库注入，不再异步替换默认 Logo。
    var brandConfig = window.__HUIYAN_BRAND_CONFIG__ || {};

    var ref = Vue.ref;
    var computed = Vue.computed;
    var reactive = Vue.reactive;
    var onMounted = Vue.onMounted;
    var onUnmounted = Vue.onUnmounted;
    var toRefs = Vue.toRefs;

    // ========== 农户端 Axios 实例（携带 farmer_token，API 前缀 /api/v1） ==========
    var request = axios.create({
        baseURL: '/api/v1',
        timeout: 60000,
        headers: { 'Content-Type': 'application/json' }
    });
    request.interceptors.request.use(function (config) {
        var token = localStorage.getItem('farmer_token');
        if (token) { config.headers.Authorization = 'Bearer ' + token; }
        return config;
    });
    request.interceptors.response.use(function (r) { return r; }, function (error) {
        if (error.response && error.response.status === 401) {
            localStorage.removeItem('farmer_token');
            if (location.pathname !== '/farmer/login') {
                location.href = '/farmer/login';
            }
        }
        return Promise.reject(error);
    });

    // ========== 侧边栏折叠状态 ==========
    var sidebarCollapsed = ref(
        (function () {
            try { return localStorage.getItem('farmer_sidebar_collapsed') === '1'; } catch (e) { return false; }
        })()
    );

    /** 切换侧边栏折叠（DOM + Vue 双同步） */
    window.__toggleFarmerSidebar = function () {
        var sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;
        sidebar.classList.toggle('collapsed');
        var collapsed = sidebar.classList.contains('collapsed');
        sidebarCollapsed.value = collapsed;
        localStorage.setItem('farmer_sidebar_collapsed', collapsed ? '1' : '0');
    };

    // ========== 侧边栏 Vue 实例（对齐管理端 layout.js） ==========
    (function initSidebar() {
        var el = document.querySelector('#sidebar-app');
        if (!el) return;

        // 菜单树数据（从 API 或 localStorage 缓存读取）
        var menuTree = ref([]);
        // 按后端返回顺序（已按 sort_order 升序）直接渲染，父级/叶子混合不拆分
        var orderedMenus = computed(function () { return menuTree.value; });

        // 受控展开态：从 localStorage 恢复，跨页面保持展开状态
        var savedExpanded = (function () {
            try { return JSON.parse(localStorage.getItem('farmer_sidebar_expanded') || '[]'); }
            catch (e) { return []; }
        })();
        var expandedMenus = ref(savedExpanded);

        // 当前激活菜单
        var activeMenu = ref('home');

        /**
         * 过滤不可见菜单（visible=false）— 服务端设置 visible=0 后侧边栏不显示
         */
        var filterVisibleMenus = function (menus) {
            return menus.filter(function (item) {
                if (item.children && item.children.length) {
                    item.children = item.children.filter(function (c) {
                        return c.visible !== false && c.visible !== 0;
                    });
                    return item.children.length > 0;
                }
                return item.visible !== false && item.visible !== 0;
            });
        };

        // 根据当前路径设置菜单高亮 + 展开父菜单
        var setActiveByPath = function (pathOverride) {
            var path = pathOverride || location.pathname;
            for (var i = 0; i < menuTree.value.length; i++) {
                var item = menuTree.value[i];
                if (item.children && item.children.length) {
                    for (var j = 0; j < item.children.length; j++) {
                        var child = item.children[j];
                        var childPath = child.path || '';
                        if (childPath && path.indexOf(childPath) !== -1) {
                            activeMenu.value = child.name || String(child.id);
                            var parentName = item.name || String(item.id);
                            if (expandedMenus.value.indexOf(parentName) === -1) {
                                expandedMenus.value = expandedMenus.value.concat([parentName]);
                            }
                            return;
                        }
                    }
                } else {
                    var itemPath = item.path || '';
                    if (itemPath && path.indexOf(itemPath) !== -1) {
                        activeMenu.value = item.name || String(item.id);
                        return;
                    }
                }
            }
        };

        // 默认菜单树（API 加载失败或缓存为空时的回退数据）
        var fallbackTree = [
            { id: 100, name: 'home', title: '首页', path: '/farmer/home', icon: 'home', parent_id: 0, visible: true, children: [] },
            { id: 101, name: 'profile', title: '个人中心', path: '/farmer/profile', icon: 'user-setting', parent_id: 0, visible: true, children: [] },
            { id: 110, name: 'system_manage', title: '系统管理', path: '', icon: 'setting', parent_id: 0, visible: true, children: [
                { id: 111, name: 'log', title: '系统日志', path: '/farmer/log', icon: 'file', visible: true },
                { id: 112, name: 'cache', title: '缓存管理', path: '/farmer/cache', icon: 'file', visible: true },
                { id: 113, name: 'system_info', title: '系统信息', path: '/farmer/system', icon: 'setting', visible: true }
            ]}
        ];

        // 加载菜单数据（stale-while-revalidate：先用缓存快速渲染，后台再刷新）
        var loadMenus = function () {
            var cached = localStorage.getItem('farmer_menus');
            if (cached) {
                try {
                    var parsed = JSON.parse(cached);
                    if (Array.isArray(parsed) && parsed.length > 0) {
                        menuTree.value = filterVisibleMenus(parsed);
                        setActiveByPath();
                    } else {
                        localStorage.removeItem('farmer_menus');
                    }
                } catch (e) { localStorage.removeItem('farmer_menus'); }
            }
            // 无论是否有缓存，都后台请求最新菜单
            request.get('/menu/tree').then(function (res) {
                var list = (res.data.data && res.data.data.list) ? res.data.data.list : [];
                if (list.length > 0) {
                    var filtered = filterVisibleMenus(list);
                    menuTree.value = filtered;
                    localStorage.setItem('farmer_menus', JSON.stringify(list));
                    setActiveByPath();
                } else if (!menuTree.value.length) {
                    menuTree.value = fallbackTree.slice();
                    setActiveByPath();
                }
            }).catch(function () {
                if (!menuTree.value.length) {
                    menuTree.value = fallbackTree.slice();
                    setActiveByPath();
                }
            });
        };

        var siteLogo = ref(brandConfig.site_logo);
        var siteFavicon = ref(brandConfig.site_favicon);
        var userInfo = reactive({ nickname: '', username: '', avatar: '' });
        var isCollapsed = sidebarCollapsed;

        // ========== 主题模式（明亮/深色） ==========
        // 从 localStorage 读取用户偏好，默认明亮模式
        var isDarkMode = ref((function () {
            try { return localStorage.getItem('farmer_theme') === 'dark'; }
            catch (e) { return false; }
        })());
        // TDesign Menu 组件的 theme 属性：明亮模式用 'light'，深色模式用 'dark'
        var sidebarTheme = computed(function () { return isDarkMode.value ? 'dark' : 'light'; });
        // 将 data-theme 写入 <html> 标签，触发 CSS 变量切换；同步持久化到 localStorage
        var applyTheme = function () {
            var theme = isDarkMode.value ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', theme);
            try { localStorage.setItem('farmer_theme', theme); } catch (e) { /* 忽略 */ }
        };

        var displayName = computed(function () {
            return userInfo.nickname || userInfo.username || '用户';
        });
        var avatarText = computed(function () {
            var name = userInfo.nickname || userInfo.username || '';
            return name.charAt(0).toUpperCase() || '?';
        });
        var userAvatar = computed(function () { return userInfo.avatar || ''; });

        /** 从 JWT 解析用户名（立即显示，无需等待 API） */
        var parseJwtUser = function () {
            try {
                var token = localStorage.getItem('farmer_token');
                if (!token) return;
                var payload = JSON.parse(atob(token.split('.')[1]));
                if (payload.name) { userInfo.username = payload.name; }
            } catch (e) { /* JWT 解析失败，忽略 */ }
        };

        // 菜单点击 — 在菜单树中查找匹配项，使用 SPA 局部加载
        var findInTree = function (items, name) {
            for (var i = 0; i < items.length; i++) {
                if (items[i].children && items[i].children.length) {
                    for (var j = 0; j < items[i].children.length; j++) {
                        if ((items[i].children[j].name || items[i].children[j].id) === name) {
                            return items[i].children[j];
                        }
                    }
                }
                if ((items[i].name || items[i].id) === name) return items[i];
            }
            return null;
        };
        var onMenuChange = function (value) {
            var item = findInTree(menuTree.value, value);
            if (!item) return;
            activeMenu.value = value;
            var href = item.path || '';
            if (href && window.HuiYanFarmer && window.HuiYanFarmer.loadPage) {
                window.HuiYanFarmer.loadPage(href);
            } else if (href) {
                location.href = href;
            }
        };

        // 子菜单展开回调
        var onExpand = function (value) {
            expandedMenus.value = value;
            localStorage.setItem('farmer_sidebar_expanded', JSON.stringify(value));
        };

        var onLogout = function () {
            localStorage.removeItem('farmer_token');
            location.href = '/farmer/login';
        };

        // 用户卡片下拉操作（hover 触发）：主题切换 + 退出登录
        var userActions = computed(function () { return [
            { content: isDarkMode.value ? '明亮模式' : '深色模式', value: 'theme-toggle' },
            { content: '退出登录', value: 'logout', theme: 'error' }
        ]; });
        var onUserAction = function (data) {
            if (data.value === 'theme-toggle') {
                isDarkMode.value = !isDarkMode.value;
                applyTheme();
                return;
            }
            if (data.value === 'logout') { onLogout(); return; }
        };

        var app = Vue.createApp({
            setup: function () {
                // onMounted 必须在 setup() 内调用，否则 Vue 无法注册生命周期钩子
                onMounted(function () {
                    // 确保主题属性已设置（与 base.html 内联脚本双保险，防闪烁）
                    applyTheme();

                    // 立即从 JWT 解析用户名（无需等待 API）
                    parseJwtUser();

                    // 从 API 或缓存加载菜单树
                    loadMenus();

                    // 用户信息（昵称、邮箱等补充字段）
                    request.get('/me').then(function (res) {
                        var d = res.data.data || res.data || {};
                        if (d.nickname) { userInfo.nickname = d.nickname; }
                        if (d.avatar) { userInfo.avatar = d.avatar; }
                        if (d.name || d.username) { userInfo.username = d.name || d.username; }
                    }).catch(function () { /* JWT 已提供用户名，忽略 API 失败 */ });

                    // 初始化折叠状态
                    var sidebar = document.querySelector('.sidebar');
                    if (sidebar && isCollapsed.value) {
                        sidebar.classList.add('collapsed');
                    }

                    // 监听 SPA 路径变化更新菜单高亮
                    if (sidebar) {
                        sidebar.addEventListener('farmer-spa-path-change', function (e) {
                            if (e.detail && e.detail.path) { setActiveByPath(e.detail.path); }
                        });
                    }
                });

                return {
                    orderedMenus: orderedMenus,
                    activeMenu: activeMenu,
                    expandedMenus: expandedMenus,
                    isCollapsed: isCollapsed,
                    sidebarTheme: sidebarTheme,
                    userActions: userActions,
                    siteLogo: siteLogo,
                    siteFavicon: siteFavicon,
                    displayName: displayName,
                    avatarText: avatarText,
                    userAvatar: userAvatar,
                    onMenuChange: onMenuChange,
                    onExpand: onExpand,
                    onUserAction: onUserAction
                };
            }
        });
        app.config.compilerOptions.delimiters = ['[[', ']]'];
        if (typeof TDesign !== 'undefined') { app.use(TDesign); }
        app.mount('#sidebar-app');
    })();

    // ========== 顶栏 Vue 实例 ==========
    (function initTopbar() {
        var el = document.querySelector('#farmer-topbar');
        if (!el) return;

        var app = Vue.createApp({
            setup: function () {
                var ref = Vue.ref;
                var onMounted = Vue.onMounted;
                var onUnmounted = Vue.onUnmounted;

                var unreadCount = ref(0);
                var pollTimer = null;

                var toggleSidebar = function () {
                    if (window.__toggleFarmerSidebar) { window.__toggleFarmerSidebar(); }
                };
                var goInbox = function () {
                    if (window.HuiYanFarmer && window.HuiYanFarmer.loadPage) {
                        window.HuiYanFarmer.loadPage('/farmer/inbox');
                    }
                };
                var fetchUnread = function () {
                    request.get('/inbox/unread-count')
                        .then(function (res) {
                            var data = res.data.data || res.data;
                            unreadCount.value = data.count || 0;
                        }).catch(function () {});
                };

                onMounted(function () {
                    fetchUnread();
                    pollTimer = setInterval(fetchUnread, 60000);
                });
                onUnmounted(function () {
                    if (pollTimer) { clearInterval(pollTimer); }
                });

                return { toggleSidebar: toggleSidebar, unreadCount: unreadCount, goInbox: goInbox };
            }
        });
        app.config.compilerOptions.delimiters = ['[[', ']]'];
        if (typeof TDesign !== 'undefined') { app.use(TDesign); }
        app.mount('#farmer-topbar');
    })();

    // ========== 页面 Vue 应用初始化（供页面脚本调用） ==========
    /**
     * 创建并挂载页面 Vue 应用
     * 根组件统一注入站点品牌（siteName / copyright），并合并页面自身 setup 返回值。
     * @param {object} pageOptions - 页面选项，支持 setup()
     * @param {string} [selector='#farmer-app'] 挂载点
     */
    function createApp(pageOptions, selector, config) {
        selector = selector || '#farmer-app';
        config = config || {};
        var el = document.querySelector(selector);
        if (!el || typeof Vue === 'undefined') { return null; }

        pageOptions = pageOptions || {};
        var userSetup = pageOptions.setup;

        var rootOptions = Object.assign({}, pageOptions, {
            setup: function () {
                // 站点品牌（公开配置，无需登录）
                var site = reactive({ siteName: brandConfig.site_name });

                var userState = typeof userSetup === 'function' ? (userSetup() || {}) : {};
                return Object.assign({}, toRefs(site), userState);
            }
        });

        var app = Vue.createApp(rootOptions);
        app.config.compilerOptions.delimiters = ['[[', ']]'];
        if (typeof TDesign !== 'undefined') { app.use(TDesign); }
        // 注册 TDesign Chat 组件库（仅 AI 对话页引入 tdesign-chat.iife.js 后存在）
        // isCustomElement 全返回 false：tdesign-web-components 注册了 t-chat-* 同名自定义元素，
        // 导致 Vue 模板里 <t-chat-item> 被当 Web Component 而非 Vue 组件；强制走 resolveComponent
        if (window.TDesignChat) {
            app.use(window.TDesignChat);
            app.config.compilerOptions.isCustomElement = function () { return false; };
        }
        // 注册项目全局自定义组件（如 AI 对话组件），供所有农户端页面直接使用
        if (window.HuiYanComponents) {
            Object.keys(window.HuiYanComponents).forEach(function (name) {
                app.component(name, window.HuiYanComponents[name]);
            });
        }
        app.mount(selector);
        return app;
    }

    /**
     * 销毁页面 Vue 应用（SPA 导航时调用）
     */
    function destroyPageApp() {
        var el = document.querySelector('#farmer-app');
        if (el && el.__vue_app__) {
            el.__vue_app__.unmount();
        }
    }

    function createPluginPage(options) {
        var element = document.getElementById('plugin-page-context');
        var context = element ? JSON.parse(element.textContent || '{}') : {};
        if (context.plugin !== options.plugin || context.page !== options.page) { return null; }
        var base = String(context.apiBase || '').replace(/^\/api\/v1/, '').replace(/\/$/, '');
        var api = {};
        ['get', 'post', 'put', 'patch', 'delete'].forEach(function (method) {
            api[method] = function (path, dataOrConfig, config) {
                var url = base + (path ? '/' + String(path).replace(/^\//, '') : '');
                if (method === 'get' || method === 'delete') {
                    return request[method](url, dataOrConfig);
                }
                return request[method](url, dataOrConfig, config);
            };
        });
        return createApp({ setup: function () {
            return options.setup({ plugin: context.plugin, page: context.page, api: api, request: request });
        }});
    }

    window.HuiYanFarmer = {
        createApp: createApp,
        createPluginPage: createPluginPage,
        destroyPageApp: destroyPageApp,
        request: request
    };
})(window);
