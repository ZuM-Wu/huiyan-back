/**
 * 农户端 SPA 局部页面加载器 — 侧边栏不动，只替换主内容区
 *
 * 工作原理（对齐管理端 spa-navigator.js，简化版）：
 *   1. 拦截侧边栏菜单点击
 *   2. fetch 目标页面 HTML
 *   3. 提取 .farmer-page-content 和 <script> 内容
 *   4. 销毁旧 Vue 实例 → 替换 DOM → 执行脚本 → 挂载新 Vue
 *   5. pushState 更新 URL + 更新 document.title
 *   6. popstate 支持浏览器前进/后退
 *
 * 依赖：farmer-app.js（HuiYanFarmer.destroyPageApp / createApp）
 */
(function () {
    'use strict';

    var _navigating = false;

    /**
     * 重申页面标题：强制触发浏览器标题变更通知（农户端不加载 hy-app.js，故本地内联）
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

    // 全局脚本白名单（base.html 固定加载的，SPA 导航时不重复注入）
    var GLOBAL_SCRIPT_PATHS = [
        '/static/vendor/vue/vue.global.prod.min.js',
        '/static/vendor/tdesign/tdesign.min.js',
        '/static/vendor/tdesign-icons/index.min.js',
        '/static/vendor/axios/axios.min.js'
    ];

    /** 判断脚本是否为全局脚本（忽略 ?v= 版本号） */
    function isGlobalScript(src) {
        var path = (src || '').split('?')[0];
        // 检查是否为全局 vendor 或 farmer-app.js / farmer-spa.js
        if (GLOBAL_SCRIPT_PATHS.indexOf(path) !== -1) return true;
        if (path.indexOf('farmer-app.js') !== -1) return true;
        if (path.indexOf('farmer-spa.js') !== -1) return true;
        return false;
    }

    /**
     * 从完整 HTML 中提取 .farmer-page-content 内容 + 页面专属脚本
     */
    function extractContent(html) {
        var parser = new DOMParser();
        var doc = parser.parseFromString(html, 'text/html');
        var contentEl = doc.querySelector('.farmer-page-content');
        if (!contentEl) { return null; }

        // 提取 #farmer-app 内部 HTML（页面内容块）
        var appEl = contentEl.querySelector('#farmer-app');
        var innerHTML = appEl ? appEl.innerHTML : contentEl.innerHTML;

        // 提取页面专属脚本
        var pageScripts = [];
        doc.querySelectorAll('body > script').forEach(function (s) {
            var src = s.getAttribute('src');
            if (src && isGlobalScript(src)) { return; }
            pageScripts.push(s.outerHTML);
        });

        // 提取页面专属 CSS <link>
        var currentHrefs = new Set();
        document.querySelectorAll('head > link[rel="stylesheet"]').forEach(function (l) {
            currentHrefs.add(l.getAttribute('href'));
        });
        var pageCssLinks = [];
        doc.querySelectorAll('head > link[rel="stylesheet"]').forEach(function (l) {
            var href = l.getAttribute('href');
            if (href && !currentHrefs.has(href)) {
                pageCssLinks.push(l.outerHTML);
            }
        });

        // 提取页面专属 <style> 块（如 certification/home/security 页内样式），避免全局污染
        var currentStyleKeys = new Set();
        document.querySelectorAll('head > style[data-farmer-page-css]').forEach(function (s) {
            currentStyleKeys.add(s.getAttribute('data-spa-style-key') || s.textContent.trim().substring(0, 120));
        });
        var pageStyles = [];
        doc.querySelectorAll('head > style').forEach(function (s) {
            // 跳过 v-cloak 等全局基础样式（已在 base.html 中）
            var text = s.textContent.trim();
            if (text.indexOf('[v-cloak]') !== -1) { return; }
            var key = s.getAttribute('data-spa-style-key') || text.substring(0, 120);
            if (!currentStyleKeys.has(key)) {
                pageStyles.push(s.outerHTML);
            }
        });

        return {
            innerHTML: innerHTML + '\n' + pageScripts.join('\n'),
            title: doc.querySelector('title') ? doc.querySelector('title').textContent : '',
            pageCssLinks: pageCssLinks,
            pageStyles: pageStyles,
            pluginPageContext: (doc.querySelector('#plugin-page-context') || {}).outerHTML || ''
        };
    }

    // 插件页面上下文位于 head，SPA 只替换主内容区时必须同步更新。
    function updatePluginPageContext(markup) {
        var current = document.getElementById('plugin-page-context');
        if (current) { current.remove(); }
        if (!markup) { return; }
        var temp = document.createElement('div');
        temp.innerHTML = markup;
        var next = temp.firstElementChild;
        if (next) { document.head.appendChild(next); }
    }

    /**
     * 按顺序执行容器内的所有 <script> 标签
     */
    function executeScripts(container) {
        var scripts = Array.from(container.querySelectorAll('script'));
        return scripts.reduce(function (chain, oldScript) {
            return chain.then(function () {
                return new Promise(function (resolve) {
                    var newScript = document.createElement('script');
                    Array.from(oldScript.attributes).forEach(function (attr) {
                        newScript.setAttribute(attr.name, attr.value);
                    });
                    var hasSrc = oldScript.hasAttribute('src');
                    if (hasSrc) {
                        newScript.async = false;
                        newScript.onload = function () { resolve(); };
                        newScript.onerror = function () { resolve(); };
                        oldScript.parentNode.replaceChild(newScript, oldScript);
                    } else {
                        newScript.textContent = oldScript.textContent;
                        oldScript.parentNode.replaceChild(newScript, oldScript);
                        resolve();
                    }
                });
            });
        }, Promise.resolve());
    }

    /** 通知侧边栏更新高亮 */
    function updateSidebarActive(path) {
        var sidebar = document.querySelector('.sidebar');
        if (sidebar) {
            sidebar.dispatchEvent(new CustomEvent('farmer-spa-path-change', { detail: { path: path } }));
        }
    }

    /** 移除旧页面专属 CSS <link> */
    function removePageCssLinks() {
        document.querySelectorAll('head > link[data-farmer-page-css]').forEach(function (l) { l.remove(); });
    }

    /** 移除旧页面专属 <style> 块 */
    function removePageStyles() {
        document.querySelectorAll('head > style[data-farmer-page-css]').forEach(function (s) { s.remove(); });
    }

    /** 注入页面专属 <style> 块到 <head>，标记 data-farmer-page-css 以便下次导航时移除 */
    function injectPageStyles(styles) {
        removePageStyles();
        if (!styles || !styles.length) { return; }
        var head = document.head;
        styles.forEach(function (styleHtml) {
            var temp = document.createElement('div');
            temp.innerHTML = styleHtml;
            var style = temp.firstChild;
            if (style) {
                style.setAttribute('data-farmer-page-css', 'true');
                head.appendChild(style);
            }
        });
    }

    /** 注入页面专属 CSS */
    function injectPageCssLinks(cssLinks) {
        removePageCssLinks();
        if (!cssLinks || !cssLinks.length) { return Promise.resolve(); }
        var head = document.head;
        var loadPromises = [];
        cssLinks.forEach(function (linkHtml) {
            var temp = document.createElement('div');
            temp.innerHTML = linkHtml;
            var link = temp.firstChild;
            if (link) {
                link.setAttribute('data-farmer-page-css', 'true');
                loadPromises.push(new Promise(function (resolve) {
                    var done = false;
                    var finish = function () { if (!done) { done = true; resolve(); } };
                    link.onload = finish;
                    link.onerror = finish;
                    setTimeout(finish, 1500);
                }));
                head.appendChild(link);
            }
        });
        return Promise.all(loadPromises);
    }

    /**
     * 核心：加载页面内容（不刷新整页）
     */
    function loadPage(url, pushHistory) {
        if (_navigating) { return; }
        _navigating = true;

        var contentEl = document.querySelector('.farmer-page-content');
        if (!contentEl) {
            window.location.href = url;
            return;
        }

        // 淡出旧内容
        contentEl.style.opacity = '0.4';
        contentEl.style.transition = 'opacity 0.15s ease';

        fetch(url, { headers: { 'X-Requested-With': 'SPA' } })
            .then(function (res) {
                if (!res.ok) { throw new Error('HTTP ' + res.status); }
                return res.text();
            })
            .then(function (html) {
                var extracted = extractContent(html);
                if (!extracted) {
                    window.location.href = url;
                    return;
                }

                // 销毁旧页面 Vue 实例
                if (window.HuiYanFarmer && window.HuiYanFarmer.destroyPageApp) {
                    window.HuiYanFarmer.destroyPageApp();
                }

                // 移除旧页面专属脚本
                document.querySelectorAll('script[data-farmer-page-script]').forEach(function (s) { s.remove(); });

                // 替换内容
                var appEl = document.querySelector('#farmer-app');
                if (appEl) {
                    appEl.innerHTML = extracted.innerHTML;
                } else {
                    contentEl.innerHTML = extracted.innerHTML;
                }
                updatePluginPageContext(extracted.pluginPageContext);

                // 标记页面脚本
                var targetEl = appEl || contentEl;
                var newScripts = targetEl.querySelectorAll('script');
                newScripts.forEach(function (s) { s.setAttribute('data-farmer-page-script', 'true'); });

                // pushState
                if (pushHistory !== false) {
                    history.pushState({ url: url }, '', url);
                }

                // 注入页面专属 <style> + <link> CSS → 执行脚本 → 完成
                injectPageStyles(extracted.pageStyles);
                injectPageCssLinks(extracted.pageCssLinks).then(function () {
                    return executeScripts(targetEl);
                }).then(function () {
                    // 移除 v-cloak
                    var pageApp = targetEl.querySelector('#farmer-app') || targetEl;
                    if (pageApp) { pageApp.removeAttribute('v-cloak'); }
                    // 更新标题并重申：赋新标题保证换页语义，重申强制派发标题变更通知，
                    // 避免浏览器在 history 导航后把标签名刷新为路径名
                    document.title = extracted.title || document.title;
                    reassertTitle();

                    // 更新侧边栏高亮
                    updateSidebarActive(url);

                    // 淡入新内容
                    requestAnimationFrame(function () {
                        contentEl.style.opacity = '1';
                        contentEl.scrollTop = 0;
                    });

                    _navigating = false;
                });
            })
            .catch(function (err) {
                console.error('[Farmer SPA] 页面加载失败:', err);
                contentEl.style.opacity = '1';
                _navigating = false;
                window.location.href = url;
            });
    }

    /** 判断是否为农户端内部链接 */
    function isInternalLink(url) {
        return url && url.startsWith('/farmer/') && !url.startsWith('/farmer/login');
    }

    // ========== 浏览器前进/后退 ==========
    window.addEventListener('popstate', function (e) {
        if (e.state && e.state.url) {
            loadPage(e.state.url, false);
        }
    });

    // 初始化：将当前 URL 写入 history state
    if (!history.state) {
        history.replaceState({ url: window.location.pathname }, '', window.location.pathname);
        // 重申页面标题，避免 replaceState 后标签名被刷新为路径名
        reassertTitle();
    }

    // 初始化：标记首屏页面专属脚本
    document.querySelectorAll('body > script[src]').forEach(function (s) {
        var src = s.getAttribute('src') || '';
        if (!isGlobalScript(src)) {
            s.setAttribute('data-farmer-page-script', 'true');
        }
    });

    // 初始化：标记首屏整页加载的页面专属 CSS（非全局样式标记为 data-farmer-page-css，
    // 使其与 SPA 注入的 CSS 遵循相同的移除/重注入循环，避免样式泄露到其他页面）
    var GLOBAL_CSS_PATHS = [
        '/static/vendor/tdesign/tdesign.min.css',
        '/static/css/theme.css',
        '/static/css/admin-base.css',
        '/static/css/admin-nav.css',
        '/static/css/admin-forms.css',
        '/static/css/admin-detail.css'
    ];
    function isGlobalCss(href) {
        var path = (href || '').split('?')[0];
        if (GLOBAL_CSS_PATHS.indexOf(path) !== -1) return true;
        // 农户端主题 CSS 路径含 theme_static 前缀，按文件名匹配
        if (path.indexOf('/css/app.css') !== -1) return true;
        return false;
    }
    document.querySelectorAll('head > link[rel="stylesheet"]').forEach(function (l) {
        var href = l.getAttribute('href') || '';
        if (!isGlobalCss(href)) {
            l.setAttribute('data-farmer-page-css', 'true');
        }
    });
    // 标记首屏页面专属 <style> 块（非 v-cloak 全局基础样式）
    document.querySelectorAll('head > style').forEach(function (s) {
        var text = s.textContent.trim();
        if (text.indexOf('[v-cloak]') !== -1) { return; }
        s.setAttribute('data-farmer-page-css', 'true');
    });

    // ========== 暴露全局 API ==========
    window.HuiYanFarmer = window.HuiYanFarmer || {};
    window.HuiYanFarmer.loadPage = loadPage;
    window.HuiYanFarmer.isInternalLink = isInternalLink;
})();
