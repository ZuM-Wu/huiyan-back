/**
 * 布局脚本
 * 负责: 侧边栏菜单动态渲染（从 API 获取，按 sort_order 保序）、菜单高亮、
 *      受控展开态（点击子页面后父级保持展开）、底部 Discord 风格用户卡片、
 *      顶栏折叠按钮与用户信息。
 *
 * 统一写法：Composition API + ES6 + HuiYan.createPage（自定义挂载点）
 * 两个独立 Vue 实例：
 *   #sidebar-app  — 侧边栏菜单 + 用户卡片
 *   #topbar-app   — 顶部栏折叠按钮
 */
(function () {
    const { ref, computed, onMounted } = Vue;

    // ========== 页面级访问守卫 ==========
    // 侧边栏已按权限隐藏无权菜单；本守卫额外拦截"直接输入 URL"访问无权限页面的情况。
    // 判定依据 localStorage 中的 admin_pages(受控页面路径→code) 与 admin_auth(用户权限 code)。
    (function enforcePageAccess() {
        // 控制台/个人设置/登录/iframe 容器为登陆落地页或非受控页，始终放行（避免重定向死循环）
        const EXEMPT = ['/admin/dashboard', '/admin/account', '/admin/login', '/admin/iframe_page'];
        const path = window.location.pathname;
        if (EXEMPT.indexOf(path) !== -1) { return; }

        const runGuard = (auth, pages) => {
            const required = pages[path];
            // 该页面受控且当前用户未获授权 → 拦截并回控制台
            if (required && auth.indexOf(required) === -1) {
                try { if (window.TDesign && TDesign.MessagePlugin) { TDesign.MessagePlugin.warning('无权限访问该页面'); } } catch (e) { /* 忽略 */ }
                window.location.replace('/admin/dashboard');
            }
        };

        const authRaw = localStorage.getItem('admin_auth');
        const pagesRaw = localStorage.getItem('admin_pages');
        if (authRaw && pagesRaw) {
            try { runGuard(JSON.parse(authRaw), JSON.parse(pagesRaw)); return; } catch (e) { /* 缓存损坏则补拉 */ }
        }
        // 缓存缺失（旧会话）：向后端补拉后再判定
        request.get('/permission/my-auth').then((res) => {
            const d = (res.data && res.data.data) || res.data || {};
            const auth = d.auth || [];
            const pages = d.pages || {};
            localStorage.setItem('admin_auth', JSON.stringify(auth));
            localStorage.setItem('admin_pages', JSON.stringify(pages));
            runGuard(auth, pages);
        }).catch(() => { /* 静默失败，放行 */ });
    })();

    // 读取本地缓存的管理员信息（登录时写入）
    const readAdminUser = () => {
        const cached = localStorage.getItem('admin_user');
        if (cached) {
            try { return JSON.parse(cached); } catch (e) { /* 忽略解析错误 */ }
        }
        return { nickname: '管理员' };
    };

    // 统一退出登录逻辑
    const doLogout = () => {
        request.post('/auth/logout').then(() => {}).catch(() => {}).finally(() => {
            ['admin_token', 'admin_user', 'admin_menus', 'admin_auth', 'admin_pages'].forEach((k) => localStorage.removeItem(k));
            window.location.href = '/admin/login';
        });
    };

    // ========== 侧边栏折叠状态 ==========
    const sidebarCollapsed = ref(
        (() => { try { return localStorage.getItem('sidebar_collapsed') === '1'; } catch (e) { return false; } })()
    );

    /**
     * 切换侧边栏折叠状态（DOM 操作 + Vue 响应式双保险）
     * 暴露到 window 上，顶栏按钮直接调用
     */
    window.__toggleSidebar = () => {
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;
        sidebar.classList.toggle('collapsed');
        const isNowCollapsed = sidebar.classList.contains('collapsed');
        sidebarCollapsed.value = isNowCollapsed;
        localStorage.setItem('sidebar_collapsed', isNowCollapsed ? '1' : '0');
    };

    // ========== 侧边栏 Vue 实例 ==========
    HuiYan.createPage({
        setup() {
            const menuTree = ref([]);
            const activeMenu = ref('');
            // 侧边栏折叠状态（从 DOM 同步）
            const isCollapsed = sidebarCollapsed;

            // 站点 Logo — 默认绝对路径（避免深层 URL 如插件页 /admin/plugin/x/y 下相对路径解析错误），从配置 API 动态加载
            const siteLogo = ref('/static/img/logo.png');
            const siteFavicon = ref('/static/img/favicon.png');
            // Logo 点击跳转地址与方式（_self=当前页面 / _blank=新页面），从配置加载
            const logoUrl = ref('');
            const logoTarget = ref('_self');
            // 受控展开态：从 localStorage 恢复，跨页面保持展开状态
            const savedExpanded = (() => {
                try { return JSON.parse(localStorage.getItem('sidebar_expanded') || '[]'); }
                catch (e) { return []; }
            })();
            // 手动展开态与路由驱动展开态分离：
            // 进入子页面时仅临时展开其父级菜单，离开该路由后自动收回，
            // 避免 AI 对话这类子页面把整列导航永久往下推移。
            const manualExpandedMenus = ref(savedExpanded);
            const routeExpandedMenu = ref('');
            const expandedMenus = computed(() => {
                const merged = new Set(manualExpandedMenus.value);
                if (routeExpandedMenu.value) {
                    merged.add(routeExpandedMenu.value);
                }
                return Array.from(merged);
            });
            const adminUser = ref(readAdminUser());

            // ========== 主题模式（明亮/深色） ==========
            // 从 localStorage 读取用户偏好，默认明亮模式（白底侧边栏）
            const isDarkMode = ref((() => {
                try { return localStorage.getItem('admin_theme') === 'dark'; }
                catch (e) { return false; }
            })());
            // TDesign Menu 组件的 theme 属性：明亮模式用 'light'，深色模式用 'dark'
            const sidebarTheme = computed(() => isDarkMode.value ? 'dark' : 'light');
            // 将 data-theme 写入 <html> 标签，触发 CSS 变量切换；同步持久化到 localStorage
            const applyTheme = () => {
                const theme = isDarkMode.value ? 'dark' : 'light';
                document.documentElement.setAttribute('data-theme', theme);
                try { localStorage.setItem('admin_theme', theme); } catch (e) { /* 忽略 */ }
            };

            // 按后端返回顺序（已按 sort_order 升序）直接渲染，父级/叶子混合不拆分
            const orderedMenus = computed(() => menuTree.value);

            // 用户卡片：角色名、头像文字（昵称首字）
            const roleName = computed(() => {
                const u = adminUser.value || {};
                return u.role_name || (u.id === 1 ? '超级管理员' : '管理员');
            });
            const avatarText = computed(() => {
                const name = (adminUser.value && adminUser.value.nickname) || '管理员';
                return name.charAt(0);
            });

            // 用户卡片下拉操作（hover 触发）：主题切换 + 账户设置 + 退出登录
            const userActions = computed(() => [
                { content: isDarkMode.value ? '明亮模式' : '深色模式', value: 'theme-toggle' },
                { content: '账户设置', value: 'profile' },
                { content: '退出登录', value: 'logout', theme: 'error' }
            ]);
            const onUserAction = (data) => {
                if (data.value === 'theme-toggle') {
                    isDarkMode.value = !isDarkMode.value;
                    applyTheme();
                    return;
                }
                if (data.value === 'logout') { doLogout(); return; }
                if (data.value === 'profile') { window.location.href = '/admin/account'; }
            };

            // 根据当前路径设置菜单高亮 + 展开父菜单（合并到已有展开态，不重置）
            const setActiveByPath = (pathOverride) => {
                const path = pathOverride || window.location.pathname;
                activeMenu.value = '';
                routeExpandedMenu.value = '';
                for (const item of menuTree.value) {
                    if (item.children && item.children.length) {
                        for (const child of item.children) {
                            const childPath = child.path || child.url || '';
                            if (childPath && path.indexOf(childPath) !== -1) {
                                activeMenu.value = child.name || child.id;
                                routeExpandedMenu.value = item.name || item.id;
                                return;
                            }
                        }
                    } else {
                        const itemPath = item.path || item.url || '';
                        if (itemPath && path.indexOf(itemPath) !== -1) {
                            activeMenu.value = item.name || item.id;
                            return;
                        }
                    }
                }
            };

            // 从接口拉取最新菜单并刷新缓存（stale-while-revalidate 的 revalidate 环节）
            const fetchMenus = () => {
                return request.get('/menu/my-tree', { params: { nav_type: 'admin' } }).then((res) => {
                    const data = res.data.data || res.data;
                    const tree = Array.isArray(data) ? data : (data.list || []);
                    const fresh = JSON.stringify(tree);
                    // 内容变化才更新，避免无谓的重渲染（如后端菜单增删后同步侧边栏）
                    if (fresh !== localStorage.getItem('admin_menus')) {
                        localStorage.setItem('admin_menus', fresh);
                        menuTree.value = filterVisibleMenus(tree);
                        setActiveByPath();
                    }
                }).catch(() => { console.warn('菜单加载失败'); });
            };

            // 加载菜单数据：优先读缓存加速首屏，随后后台拉取最新覆盖（防止后端增删菜单后侧边栏长期陈旧）
            const loadMenus = () => {
                const cached = localStorage.getItem('admin_menus');
                if (cached) {
                    try {
                        const parsed = JSON.parse(cached);
                        menuTree.value = filterVisibleMenus(parsed);
                        setActiveByPath();
                        fetchMenus();
                        return;
                    } catch (e) { /* 缓存损坏则走接口 */ }
                }
                fetchMenus();
            };

            /**
             * 过滤不可见菜单（visible=false）— 服务端设置 visible=0 后侧边栏不显示
             */
            const filterVisibleMenus = (menus) => {
                return menus.filter(item => {
                    if (item.children && item.children.length) {
                        // 过滤子菜单中的不可见项
                        item.children = item.children.filter(c => c.visible !== false && c.visible !== 0);
                        // 父级菜单无可见子项时整体隐藏
                        return item.children.length > 0;
                    }
                    return item.visible !== false && item.visible !== 0;
                });
            };

            // 菜单点击 — 根据 target_type 决定打开方式
            const onMenuChange = (value) => {
                const findItem = (items, name) => {
                    for (const item of items) {
                        if (item.children && item.children.length) {
                            for (const child of item.children) {
                                if ((child.name || child.id) === name) return child;
                            }
                        }
                        if ((item.name || item.id) === name) return item;
                    }
                    return null;
                };
                const node = findItem(menuTree.value, value);
                if (!node) return;

                const href = node.path || node.url || '';
                const isExternal = href && (href.startsWith('http://') || href.startsWith('https://'));
                const targetType = node.target_type || '';

                // 立即更新选中状态（所有类型的菜单点击都高亮当前项）
                activeMenu.value = value;

                if (isExternal) {
                    if (targetType === '_blank') {
                        window.open(href, '_blank');
                        return;
                    } else if (targetType === 'iframe') {
                        // iframe 通过 SPA 局部加载
                        const iframeUrl = '/admin/iframe_page?url=' + encodeURIComponent(href);
                        if (window.HuiYan && window.HuiYan.loadPage) {
                            window.HuiYan.loadPage(iframeUrl);
                        } else {
                            window.location.href = iframeUrl;
                        }
                        return;
                    } else {
                        window.open(href, '_blank');
                        return;
                    }
                }

                // 内部页面：优先 SPA 局部加载（侧边栏不刷新）
                if (href && window.HuiYan && window.HuiYan.loadPage) {
                    window.HuiYan.loadPage(href);
                } else if (href) {
                    window.location.href = href;
                }
            };

            // 展开态受控：手动展开/收起时同步到 localStorage
            const onExpand = (value) => {
                // 仅持久化用户手动操作的展开态，过滤掉当前路由自动展开的父级，
                // 否则进入任一子页面后该父级会永久留在展开集合中。
                manualExpandedMenus.value = value.filter((name) => name !== routeExpandedMenu.value);
                localStorage.setItem('sidebar_expanded', JSON.stringify(manualExpandedMenus.value));
            };

            // 加载站点 Logo 和名称（从配置 API）
            const loadSiteConfig = () => {
                request.get('/config/list').then((res) => {
                    const data = res.data.data || res.data;
                    const map = {};
                    (data.list || []).forEach(item => { map[item.key] = item.value; });
                    if (map.site_logo) { siteLogo.value = map.site_logo; }
                    if (map.site_favicon) {
                        siteFavicon.value = map.site_favicon;
                        // 动态设置浏览器标签页 Favicon（base.html 中为默认占位）
                        var favEl = document.querySelector("link[rel='icon']");
                        if (favEl) { favEl.href = map.site_favicon; }
                    }
                    // Logo 跳转地址与方式（地址为空则 logo 不可点击）
                    logoUrl.value = map.site_logo_url || '';
                    logoTarget.value = map.site_logo_target || '_self';
                }).catch(() => { /* 静默失败，使用默认值 */ });
            };

            onMounted(() => {
                // 确保主题属性已设置（与 base.html 内联脚本双保险，防闪烁）
                applyTheme();
                loadMenus();
                loadSiteConfig();
                // 初始化时从 localStorage 同步折叠状态到 DOM
                const sidebar = document.querySelector('.sidebar');
                if (sidebar && sidebarCollapsed.value) {
                    sidebar.classList.add('collapsed');
                }
                // 监听 SPA 路径变化事件（由 spa-navigator.js 触发）
                if (sidebar) {
                    sidebar.addEventListener('spa-path-change', (e) => {
                        if (e.detail && e.detail.path) {
                            setActiveByPath(e.detail.path);
                        }
                    });
                }
            });
            return {
                orderedMenus, activeMenu, expandedMenus,
                adminUser, roleName, avatarText, userActions,
                siteLogo, siteFavicon, isCollapsed,
                logoUrl, logoTarget, sidebarTheme,
                onMenuChange, onExpand, onUserAction
            };
        }
    }, '#sidebar-app');

    // ========== 顶栏 Vue 实例 ==========
    HuiYan.createPage({
        setup() {
            const adminUser = ref(readAdminUser());

            const toggleSidebar = () => {
                // 调用全局切换函数（DOM + Vue 双同步）
                if (window.__toggleSidebar) window.__toggleSidebar();
            };

            return { adminUser, toggleSidebar };
        }
    }, '#topbar-app');
})();
