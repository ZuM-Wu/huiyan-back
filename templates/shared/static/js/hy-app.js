/**
 * 共享引导脚本 — 统一所有后台页面 Vue 应用的初始化 + SPA 局部加载
 *
 * 消除各页面重复的样板代码：delimiters 设置、app.use(TDesign)、
 * v-permission 指令注册、mount 挂载。所有页面统一通过 HuiYan.createPage 创建。
 *
 * 依赖：base.html 已按顺序加载 Vue / TDesign / TDesign-Icons。
 *
 * 用法（统一 Composition API 写法）：
 *   const { ref, reactive, onMounted } = Vue;
 *   HuiYan.createPage({
 *       setup() {
 *           const list = ref([]);
 *           onMounted(() => { ... });
 *           return { list };
 *       }
 *   });
 *   // 默认挂载到 #page-app，如需自定义挂载点传第二参数
 */
(function (window) {
    'use strict';

    // 读取当前登录管理员的权限码集合（登录成功后写入 localStorage）
    function getAuthCodes() {
        try {
            return JSON.parse(localStorage.getItem('admin_auth') || '[]');
        } catch (e) {
            return []; }
    }

    // 读取服务端内嵌的页面数据（模板中 <script id="page-data" type="application/json">）
    function getPageData() {
        var el = document.getElementById('page-data');
        if (!el) { return {}; }
        try { return JSON.parse(el.textContent || '{}'); }
        catch (e) { return {}; }
    }

    // 当前页面 Vue 应用实例引用（用于 SPA 切换时销毁旧实例）
    var _pageApps = [];
    var _localIconMap = null;

    /**
     * 用本地 TDesign Icons Vue 组件承接 t-icon，避免 Icon 组件加载在线 SVG symbol 脚本。
     * TDesign Icons 的 IIFE 产物导出的是 XxxIcon 组件，而不是 iconfont symbol，
     * 因此在应用级将 name（kebab-case）映射为对应的本地组件。
     */
    function registerLocalIcon(app) {
        var iconLib = window.TDesignIconVueNext;
        if (!iconLib || !iconLib.manifest || !Vue.h) { return; }
        if (!_localIconMap) {
            _localIconMap = {};
            iconLib.manifest.forEach(function (item) {
                if (item && item.stem && item.icon && iconLib[item.icon + 'Icon']) {
                    _localIconMap[item.stem] = iconLib[item.icon + 'Icon'];
                }
            });
        }
        app.component('t-icon', {
            name: 'HuiYanLocalTIcon',
            props: {
                name: { type: String, default: '' },
                size: { type: [String, Number], default: undefined },
                loadDefaultIcons: { type: Boolean, default: false },
                onClick: { type: Function, default: undefined },
            },
            setup: function (props, context) {
                return function () {
                    var icon = _localIconMap[props.name] || _localIconMap[String(props.name).toLowerCase()];
                    if (!icon) {
                        return Vue.h('span', Object.assign({}, context.attrs, {
                            class: ['t-icon', 't-icon-' + (props.name || 'unknown'), context.attrs.class],
                            'aria-hidden': 'true',
                        }));
                    }
                    var attrs = Object.assign({}, context.attrs);
                    if (props.size !== undefined) { attrs.size = props.size; }
                    if (props.onClick) { attrs.onClick = props.onClick; }
                    return Vue.h(icon, attrs);
                };
            },
        });
    }

    /**
     * 创建并挂载一个页面级 Vue 应用
     * @param {object} options - 传给 Vue.createApp 的选项（推荐使用 setup）
     * @param {string} [selector='#page-app'] - 挂载点选择器
     * @param {object} [config] - 预留配置参数
     * @returns {object|null} Vue app 实例；挂载点不存在或 Vue 未加载时返回 null
     */
    function createPage(options, selector, config) {
        selector = selector || '#page-app';
        config = config || {};
        var el = document.querySelector(selector);
        if (!el) { return null; }
        if (typeof Vue === 'undefined') {
            console.error('[HuiYan] Vue 未加载，无法创建页面应用');
            return null;
        }

        var app = Vue.createApp(options || {});

        // 统一 Vue 分隔符为 [[ ]]，避免与 Jinja2 的 {{ }} 冲突
        app.config.compilerOptions.delimiters = ['[[', ']]'];

        // TDesign Icon 默认会挂载在线 iconfont 脚本；项目只允许本地资源，统一关闭该回退。
        if (window.TDesign && window.TDesign.Icon && window.TDesign.Icon.props
            && window.TDesign.Icon.props.loadDefaultIcons) {
            window.TDesign.Icon.props.loadDefaultIcons.default = false;
        }

        // 注册 TDesign 组件库
        if (typeof TDesign !== 'undefined') {
            app.use(TDesign);
        }
        // 注册本地图标组件，避免 TDesign 在未找到图标插件时回退到远程 iconfont。
        if (window.TDesignIconVueNext) {
            app.use(window.TDesignIconVueNext);
            registerLocalIcon(app);
        }

        // 注册 TDesign Chat 组件库（仅 AI 对话页引入 tdesign-chat.iife.js 后存在）
        // isCustomElement 全返回 false：tdesign-web-components 注册了 t-chat-* 同名自定义元素，
        // 导致 Vue 模板里 <t-chat-item> 被当 Web Component 而非 Vue 组件；强制走 resolveComponent
        if (window.TDesignChat) {
            app.use(window.TDesignChat);
            app.config.compilerOptions.isCustomElement = function () { return false; };
        }

        // 注册项目全局自定义组件（如 image-upload），供所有页面直接使用
        if (window.HuiYanComponents) {
            Object.keys(window.HuiYanComponents).forEach(function (name) {
                app.component(name, window.HuiYanComponents[name]);
            });
        }

        // 全局权限指令 v-permission：无对应权限码则移除该元素
        var authCodes = getAuthCodes();
        app.directive('permission', {
            mounted: function (el, binding) {
                if (binding.value && authCodes.indexOf(binding.value) === -1) {
                    if (el.parentNode) { el.parentNode.removeChild(el); }
                }
            }
        });

        app.mount(selector);

        // 跟踪页面级应用（侧边栏/顶栏不跟踪，只有 #page-app 追踪）
        if (selector === '#page-app') {
            app.__huiYanActive = true;
            _pageApps.push(app);
        }
        return app;
    }

    /**
     * 销毁当前页面级 Vue 应用（侧边栏/顶栏不受影响）
     */
    function destroyPageApps() {
        _pageApps.forEach(function (app) {
            // 先标记失效，再触发 Vue 卸载钩子；页面异步任务可据此停止更新旧 DOM。
            app.__huiYanActive = false;
            try { app.unmount(); } catch (e) { /* 忽略 */ }
        });
        _pageApps = [];
    }

    function getPluginPageContext() {
        var el = document.getElementById('plugin-page-context');
        if (!el) { return null; }
        try { return JSON.parse(el.textContent || '{}'); }
        catch (e) { return null; }
    }

    /**
     * 创建插件页面，并注入清单上下文与相对 API client。
     * setup 签名为 setup({ plugin, page, permission, api, request })。
     */
    function createPluginPage(options, selector) {
        options = options || {};
        var context = getPluginPageContext();
        if (!context || context.plugin !== options.plugin || context.page !== options.page) {
            console.error('[HuiYan] 插件页面上下文与声明不一致');
            return null;
        }
        // request 已配置 /api/admin/v1 基址，清单保存的是完整站内路径，
        // 这里只保留插件相对前缀，避免拼成 /api/admin/v1/api/admin/v1/...
        var apiBase = String(context.apiBase || '')
            .replace(/^\/api\/admin\/v1/, '')
            .replace(/\/$/, '');
        var globalRequest = typeof request !== 'undefined' ? request : window.request;
        if (!globalRequest) {
            console.error('[HuiYan] 公共请求客户端未加载');
            return null;
        }
        var api = {};
        ['get', 'post', 'put', 'patch', 'delete'].forEach(function (method) {
            api[method] = function (path, dataOrConfig, config) {
                var normalized = path ? '/' + String(path).replace(/^\//, '') : '';
                if (method === 'get' || method === 'delete') {
                    return globalRequest[method](apiBase + normalized, dataOrConfig);
                }
                return globalRequest[method](apiBase + normalized, dataOrConfig, config);
            };
        });
        var setup = options.setup;
        return createPage({
            setup: function () {
                try {
                    return setup({
                        plugin: context.plugin, page: context.page,
                        permission: context.permission || '', api: api,
                        request: globalRequest,
                    });
                } catch (error) {
                    console.error('[HuiYan] 插件页面初始化失败', error);
                    if (window.TDesign && TDesign.MessagePlugin) {
                        TDesign.MessagePlugin.error('插件页面初始化失败');
                    }
                    throw error;
                }
            },
        }, selector || '#page-app');
    }

    // ========== Tab 状态与 URL 同步 ==========
    /**
     * 从 URL 查询参数 ?tab= 读取初始 Tab 值（刷新/直达链接时停留在原 Tab）
     * @param {string} defaultTab - 默认 Tab 值
     * @param {Array<string>} allowed - 合法 Tab 值列表，URL 值不在其中时回落默认值
     * @returns {string} 初始 Tab 值
     */
    function getUrlTab(defaultTab, allowed) {
        var tab = new URLSearchParams(window.location.search).get('tab');
        if (tab && (!allowed || allowed.indexOf(tab) !== -1)) {
            return tab;
        }
        return defaultTab;
    }

    /**
     * 重申页面标题：强制触发浏览器标题变更通知
     * 部分嵌入式浏览器（如 IDE 预览）在 history API 导航后会用 URL 刷新标签名，
     * 而 Chromium 对同值重赋 document.title 不派发变更通知，
     * 因此追加零宽空格再立即恢复，使标题值真实变化两次、标签名恢复为页面标题。
     * 双次延迟（0ms + 150ms）确保执行排在浏览器用 URL 刷新标签名之后；
     * 回调执行时才读取当前标题，SPA 换页竞态下总是重申最新标题（last-write-wins）。
     */
    function reassertTitle() {
        [0, 150].forEach(function (delay) {
            setTimeout(function () {
                var t = document.title;
                document.title = t + '\u200B'; // 零宽空格：真实值变化，触发第一次通知
                document.title = t;            // 立即恢复原值，第二次通知携带正确标题
            }, delay);
        });
    }

    /**
     * Tab 切换时将当前 Tab 写回 URL 查询参数（replaceState 不新增历史记录）
     * 保留其他查询参数（如 ?id=）；state 中同步写 url 字段，
     * 与 spa-navigator.js 的 popstate/刷新逻辑保持兼容
     * @param {string} value - 当前 Tab 值
     */
    function syncUrlTab(value) {
        var url = new URL(window.location.href);
        url.searchParams.set('tab', value);
        var newUrl = url.pathname + url.search + url.hash;
        history.replaceState({ url: newUrl }, '', newUrl);
        // 重申页面标题，避免浏览器在 history 导航后把标签名刷新为路径名
        reassertTitle();
    }

    // 暴露全局命名空间 HuiYan
    window.HuiYan = {
        createPage: createPage,
        createPluginPage: createPluginPage,
        destroyPageApps: destroyPageApps,
        getPageData: getPageData,
        getAuthCodes: getAuthCodes,
        getUrlTab: getUrlTab,
        syncUrlTab: syncUrlTab,
        reassertTitle: reassertTitle
    };
})(window);
