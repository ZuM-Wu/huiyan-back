/**
 * SPA 局部页面加载器 — 侧边栏/顶栏不动，只替换主内容区
 *
 * 工作原理：
 *   1. 拦截侧边栏内部链接点击
 *   2. fetch 目标页面 HTML
 *   3. 提取 .page-content 和 <script> 内容
 *   4. 销毁旧 Vue 实例 → 替换 DOM → 执行脚本 → 挂载新 Vue
 *   5. pushState 更新 URL + 更新 document.title
 *   6. popstate 支持浏览器前进/后退
 *
 * 依赖：hy-app.js (HuiYan.destroyPageApps)、layout.js (侧边栏更新高亮)
 */
(function () {
    'use strict';

    // 导航状态
    var _navigating = false;

    // 全局脚本白名单（base.html 固定按序加载，SPA 导航时不重复注入/执行）。
    // 与 GLOBAL_CSS_PATHS 同理：判定"是否全局脚本"须用固定白名单，
    // 不能依赖当前 DOM 现存脚本——否则"整页加载的入口页"其页面脚本会残留在
    // body 直接子级，返回该页时被误判为全局脚本而跳过执行，导致 Vue 不再挂载。
    var GLOBAL_SCRIPT_PATHS = [
        '/static/vendor/vue/vue.global.prod.min.js',
        '/static/vendor/tdesign/tdesign.min.js',
        '/static/vendor/tdesign-icons/index.min.js',
        '/static/vendor/axios/axios.min.js',
        '/static/js/request.js',
        '/static/js/hy-app.js',
        '/static/js/upload-limits.js',
        '/static/js/components/image-upload.js',
        '/static/js/components/amap-palette.js',
        '/static/js/components/amap-picker.js',
        '/static/js/components/amap-board.js',
        '/static/js/components/amap-thumb.js',
        '/static/js/components/com-tinymce.js',
        '/static/js/components/ai-chat-loading.js',
        '/static/js/components/ai-markdown-view.js',
        '/static/js/components/ai-reasoning-block.js',
        '/static/js/components/ai-image-grid.js',
        '/static/js/components/ai-tool-bubble.js',
        '/static/js/components/ai-message-bubble.js',
        '/static/js/components/ai-message-list.js',
        '/static/js/components/ai-chat-sender.js',
        '/static/js/spa-navigator.js',
        '/static/js/layout.js'
    ];

    // 页面切换时这些基础样式始终保留，其余样式按目标页面声明重新注入。
    var GLOBAL_CSS_PATHS = [
        '/static/vendor/tdesign/tdesign.min.css',
        '/static/css/theme.css',
        '/static/css/admin-base.css',
        '/static/css/admin-nav.css',
        '/static/css/admin-forms.css',
        '/static/css/admin-detail.css',
        '/static/css/variable-hint.css'
    ];

    // 判断脚本 src 是否为全局脚本（忽略 ?v= 版本号）
    function isGlobalScript(src) {
        var path = (src || '').split('?')[0];
        return GLOBAL_SCRIPT_PATHS.indexOf(path) !== -1;
    }

    /**
     * 从完整 HTML 字符串中提取 .page-content 内容 + 页面专属脚本
     * 页面脚本在 {% block scripts %} 中，位于 .page-content 外部
     * 需要单独提取并拼接到内容后面，确保 Vue 挂载时能执行
     */
    function extractContent(html) {
        var parser = new DOMParser();
        var doc = parser.parseFromString(html, 'text/html');
        var contentEl = doc.querySelector('.page-content');
        if (!contentEl) { return null; }

        var innerHTML = contentEl.innerHTML;

        // 提取响应 HTML 中的页面专属脚本：src 不在全局白名单内即为页面脚本，需重新执行。
        // 用固定白名单判定（而非当前 DOM 现存脚本），避免入口页脚本残留导致返回时被跳过。
        var pageScripts = [];
        doc.querySelectorAll('body > script').forEach(function (s) {
            var src = s.getAttribute('src');
            if (src && isGlobalScript(src)) { return; } // 跳过全局脚本（base.html 已加载）
            pageScripts.push(s.outerHTML);
        });

        // 提取目标页面声明的全部专属 CSS。注入阶段会先移除当前页面专属 CSS，
        // 因此即使两个页面共用同一 href，也必须把该链接带入下一页面。
        var pageCssLinks = [];
        doc.querySelectorAll('head > link[rel="stylesheet"]').forEach(function (l) {
            var href = l.getAttribute('href') || '';
            var path = href.split('?')[0];
            if (href && GLOBAL_CSS_PATHS.indexOf(path) === -1) {
                pageCssLinks.push(l.outerHTML);
            }
        });

        // 内联页面样式同样按目标页面完整声明，避免相同样式键在切换时被误删。
        var pageStyles = [];
        doc.querySelectorAll('head > style').forEach(function (s) {
            // 跳过 v-cloak 等全局基础样式（已在 base.html 中）
            var text = s.textContent.trim();
            if (text.indexOf('[v-cloak]') !== -1) { return; }
            pageStyles.push(s.outerHTML);
        });

        return {
            innerHTML: innerHTML + '\n' + pageScripts.join('\n'),
            title: doc.querySelector('title') ? doc.querySelector('title').textContent : '',
            pageCssLinks: pageCssLinks,
            pageStyles: pageStyles,
            pluginPageContext: (doc.querySelector('#plugin-page-context') || {}).outerHTML || ''
        };
    }

    // 插件页面上下文位于 head，SPA 只替换主内容区时必须同步更新，
    // 否则 createPluginPage 会继续读取上一个页面的插件和 API 声明。
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
     * innerHTML 插入的 script 不会自动执行，需要手动创建新元素
     * 外部脚本（有 src）必须按顺序加载，确保依赖先于页面脚本执行
     * @returns {Promise} 所有脚本执行完毕后 resolve
     */
    function executeScripts(container) {
        var scripts = Array.from(container.querySelectorAll('script'));
        // 链式加载：前一个脚本完成后再加载下一个
        return scripts.reduce(function (chain, oldScript) {
            return chain.then(function () {
                return new Promise(function (resolve) {
                    var newScript = document.createElement('script');
                    // 复制属性
                    Array.from(oldScript.attributes).forEach(function (attr) {
                        newScript.setAttribute(attr.name, attr.value);
                    });
                    var hasSrc = oldScript.hasAttribute('src');
                    if (hasSrc) {
                        // 外部脚本：强制同步顺序（async=false 保证按 DOM 顺序执行）
                        newScript.async = false;
                        newScript.onload = function () { resolve(); };
                        newScript.onerror = function () { resolve(); };
                        oldScript.parentNode.replaceChild(newScript, oldScript);
                    } else {
                        // 内联脚本：复制内容后立即执行
                        newScript.textContent = oldScript.textContent;
                        oldScript.parentNode.replaceChild(newScript, oldScript);
                        resolve();
                    }
                });
            });
        }, Promise.resolve());
    }

    /**
     * 更新侧边栏高亮状态（通知侧边栏 Vue 实例当前路径变了）
     */
    function updateSidebarActive(path) {
        // 侧边栏 Vue 实例通过 DOM 事件通信
        var sidebar = document.querySelector('.sidebar');
        if (sidebar) {
            sidebar.dispatchEvent(new CustomEvent('spa-path-change', { detail: { path: path } }));
        }
    }

    // ========== SPA 页面专属 CSS 管理 ==========
    /**
     * 移除上次 SPA 导航注入的页面专属 CSS <link>
     */
    function removePageCssLinks() {
        document.querySelectorAll('head > link[data-spa-page-css]').forEach(function (l) {
            l.remove();
        });
    }

    /**
     * 注入页面专属 CSS <link> 到 <head>，标记 data-spa-page-css 以便下次导航时移除
     * 返回 Promise：待所有样式表加载完成（或超时兑底）后 resolve，
     * 保证 CSS 就绪后再挂载 Vue，避免 TDesign 在样式未加载时计算出错误布局
     */
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
                link.setAttribute('data-spa-page-css', 'true');
                loadPromises.push(new Promise(function (resolve) {
                    var done = false;
                    var finish = function () { if (!done) { done = true; resolve(); } };
                    link.onload = finish;
                    link.onerror = finish;
                    // 兜底：最多等待 1500ms，避免样式加载异常导致页面一直不渲染
                    setTimeout(finish, 1500);
                }));
                head.appendChild(link);
            }
        });
        return Promise.all(loadPromises);
    }

    /**
     * 移除上次 SPA 导航注入的页面专属 <style>
     */
    function removePageStyles() {
        document.querySelectorAll('head > style[data-spa-page-css]').forEach(function (s) {
            s.remove();
        });
    }

    /**
     * 注入页面专属 <style> 到 <head>，标记 data-spa-page-css 以便下次导航时移除
     */
    function injectPageStyles(styles) {
        removePageStyles();
        if (!styles || !styles.length) { return; }
        var head = document.head;
        styles.forEach(function (styleHtml) {
            var temp = document.createElement('div');
            temp.innerHTML = styleHtml;
            var style = temp.firstChild;
            if (style) {
                style.setAttribute('data-spa-page-css', 'true');
                head.appendChild(style);
            }
        });
    }

    /**
     * 核心：加载页面内容（不刷新整页）
     * @param {string} url - 目标页面 URL（如 /admin/user）
     * @param {boolean} [pushHistory=true] - 是否 pushState
     */
    function loadPage(url, pushHistory) {
        if (_navigating) { return; }
        _navigating = true;

        var contentEl = document.querySelector('.page-content');
        if (!contentEl) {
            window.location.href = url;
            return;
        }

        // 显示加载状态（淡出旧内容）
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
                    // 无法提取内容，回退到全页跳转
                    window.location.href = url;
                    return;
                }

                // 1. 销毁旧页面 Vue 实例
                HuiYan.destroyPageApps();

                // 2. 移除旧页面专属脚本（含首屏整页加载遗留在 body 的入口页脚本 +
                //    上次 SPA 注入在 .page-content 内的脚本），避免重复执行 createPage
                //    或残留脚本被误判为全局脚本导致返回该页时不再执行
                document.querySelectorAll('script[data-page-script]').forEach(function (s) { s.remove(); });

                // 3. 替换内容 + 拼接新页面脚本
                contentEl.innerHTML = extracted.innerHTML;
                updatePluginPageContext(extracted.pluginPageContext);

                // 3.5 注入页面专属 <style>（同步）
                injectPageStyles(extracted.pageStyles);

                // 4. 标记页面专属脚本以便下次导航时移除
                var newScripts = contentEl.querySelectorAll('script');
                newScripts.forEach(function (s) {
                    s.setAttribute('data-page-script', 'true');
                });

                // 5. 先 pushState 更新 URL，确保脚本执行时能读到正确的查询参数
                if (pushHistory !== false) {
                    history.pushState({ url: url }, '', url);
                }
                // 6. 先等待页面专属 CSS（如 system-settings.css）加载完成，再顺序执行脚本挂载 Vue，
                //    避免 CSS 未就绪时 TDesign 计算出错误布局，导致「切回页面样式崩」
                injectPageCssLinks(extracted.pageCssLinks).then(function () {
                    return executeScripts(contentEl);
                }).then(function () {
                    // 4. SPA 替换内容后移除 v-cloak，确保页面可见
                    var pageApp = contentEl.querySelector('#page-app');
                    if (pageApp) { pageApp.removeAttribute('v-cloak'); }
                    // 更新标题并重申：赋新标题保证换页语义，重申强制派发标题变更通知，
                    // 避免浏览器在 history 导航后把标签名刷新为路径名
                    document.title = extracted.title || document.title;
                    HuiYan.reassertTitle();

                    // 7. 更新侧边栏高亮
                    updateSidebarActive(url);

                    // 7. 淡入新内容
                    requestAnimationFrame(function () {
                        contentEl.style.opacity = '1';
                        contentEl.scrollTop = 0;
                    });

                    _navigating = false;
                });
            })
            .catch(function (err) {
                console.error('[SPA] 页面加载失败:', err);
                contentEl.style.opacity = '1';
                _navigating = false;
                // 回退到全页跳转
                window.location.href = url;
            });
    }

    /**
     * 判断 URL 是否为内部链接（/admin/xxx）
     */
    function isInternalLink(url) {
        return url && url.startsWith('/admin/') && !url.startsWith('/admin/login');
    }

    // ========== 浏览器前进/后退 ==========
    window.addEventListener('popstate', function (e) {
        if (e.state && e.state.url) {
            loadPage(e.state.url, false);
        }
    });

    // ========== 初始化：将当前 URL 写入 history state ==========
    // 必须保留查询参数（如 /admin/area-detail?area_id=1），否则整页加载/刷新时
    // replaceState 会把 URL 截断为纯路径，导致页面脚本读不到 area_id 等查询参数。
    if (!history.state) {
        var initialUrl = window.location.pathname + window.location.search + window.location.hash;
        history.replaceState({ url: initialUrl }, '', initialUrl);
        // 重申页面标题，避免 replaceState 后标签名被刷新为路径名
        HuiYan.reassertTitle();
    }

    // ========== 初始化：标记首次整页加载时的页面专属 CSS ==========
    // base.html 固定加载的全局样式；其余 head 样式视为页面专属（如 system-settings.css）。
    // 标记后，它们与 SPA 注入的 CSS 遵循相同的移除/重注入循环，
    // 避免“首次整页加载的页面 CSS 永不移除”导致样式泄露到其他页面。
    document.querySelectorAll('head > link[rel="stylesheet"]').forEach(function (l) {
        var href = l.getAttribute('href') || '';
        var path = href.split('?')[0];
        if (GLOBAL_CSS_PATHS.indexOf(path) === -1) {
            l.setAttribute('data-spa-page-css', 'true');
        }
    });

    // 首屏完整 HTML 中的页面内联样式也必须进入 SPA 生命周期；否则从该页离开时
    // 样式会永久残留在 head，污染后续局部加载页面。v-cloak 是 base.html 全局样式。
    document.querySelectorAll('head > style').forEach(function (s) {
        var text = s.textContent.trim();
        if (!text || text.indexOf('[v-cloak]') !== -1) { return; }
        s.setAttribute('data-spa-page-css', 'true');
        if (!s.getAttribute('data-spa-style-key')) {
            s.setAttribute('data-spa-style-key', text.substring(0, 120));
        }
    });

    // ========== 初始化：标记首屏整页加载的页面专属脚本 ==========
    // block scripts 渲染为 body 直接子级（在 .page-content 之外）。若不标记，
    // 离开该页后它会残留在 body，返回时被当作全局脚本跳过而不再执行 createPage。
    // 标记 data-page-script 后，其与 SPA 注入脚本遵循相同的移除循环。
    document.querySelectorAll('body > script[src]').forEach(function (s) {
        var src = s.getAttribute('src') || '';
        if (!isGlobalScript(src)) {
            s.setAttribute('data-page-script', 'true');
        }
    });

    // ========== 暴露全局 API ==========
    window.HuiYan = window.HuiYan || {};
    window.HuiYan.loadPage = loadPage;
    window.HuiYan.isInternalLink = isInternalLink;
})();
