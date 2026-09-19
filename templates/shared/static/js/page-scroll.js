/**
 * 矮窗口滚动兜底脚本
 *
 * 背景：管理端内容区 .page-content 固定高度，列表页的「表格抢剩余高度」链在窗口变矮时
 * 会把标签页 / 表格压到 0-2px；TDesign .t-tabs 自带 overflow:hidden，被压缩后内容既不可见
 * 也无法滚动。本脚本按实际裁剪量测结果给 #page-app 加 is-page-scroll，由 shared CSS 切到
 * 「自然高度 + 整页滚动」兜底；窗口恢复、内容能正常承载后再退出兜底。
 *
 * 约束：
 *   1. 只处理管理端 .page-content；农户端 .farmer-page-content 已有整页滚动，不参与。
 *   2. 不新增公开 API，页面不得手写 is-page-scroll；类名由本脚本独占管理。
 *   3. 量测在 requestAnimationFrame 中执行，且只在兜底态下做一次「按填充模式复测」，
 *      避免 ResizeObserver / MutationObserver 与类名切换互相触发。
 */
(function () {
    'use strict';

    var CONTAINER_SELECTOR = '.page-content';
    var PAGE_SELECTOR = '#page-app';
    var FALLBACK_CLASS = 'is-page-scroll';

    // 结构性滚动 / 裁剪容器：这些容器隐藏溢出且内容更高时，说明页面内容不可达
    var STRUCTURAL_SELECTOR = [
        '#page-app > .t-card',
        '.t-card__body',
        '.t-tabs',
        '.t-tabs__content',
        '.t-tab-panel',
        '.t-table',
        '.log-panel-content',
        '.hardware-view-content',
        '.hardware-device-table'
    ].join(',');

    // 装饰性裁剪（地图、地图标记、图片形状、富文本浮层）不属于内容不可达
    var IGNORE_SELECTOR = [
        '[data-scroll-ignore]',
        '.amap-container',
        '.amap-layers',
        '.amap-marker',
        '.hardware-map-marker',
        '.t-image__wrapper',
        '.tox',
        '.t-tooltip',
        '.t-popup'
    ].join(',');

    // 允许的亚像素误差，避免 1px 舍入误判为裁剪
    var CLIP_TOLERANCE = 2;

    var scheduled = false;
    var resizeObserver = null;
    var observedPage = null;
    var mutationObserver = null;

    /**
     * 判断内容区是否存在「隐藏溢出且内容更高」的结构性裁剪。
     * 只有这类裁剪才会让内容既看不见也滚不到；内层 overflow:auto 的滚动区不算。
     */
    function hasStructuralClip(container) {
        var nodes = container.querySelectorAll(STRUCTURAL_SELECTOR);
        for (var i = 0; i < nodes.length; i += 1) {
            var el = nodes[i];
            if (el.closest(IGNORE_SELECTOR)) { continue; }
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') { continue; }
            if (style.overflowY !== 'hidden' && style.overflowY !== 'clip') { continue; }
            if (el.scrollHeight - el.clientHeight > CLIP_TOLERANCE) { return true; }
        }
        return false;
    }

    /**
     * 让 ResizeObserver 始终跟随当前页面的 #page-app（SPA 切换会整体替换该节点）。
     */
    function syncResizeTarget(page) {
        if (!resizeObserver || observedPage === page) { return; }
        if (observedPage) {
            try { resizeObserver.unobserve(observedPage); } catch (e) { /* 节点已移除 */ }
        }
        observedPage = page;
        if (page) { resizeObserver.observe(page); }
    }

    /**
     * 一次完整判定：命中结构性裁剪则进入兜底；已处于兜底时先按填充模式复测，能承载即退出。
     */
    function evaluate() {
        var container = document.querySelector(CONTAINER_SELECTOR);
        if (!container) { return; }
        var page = container.querySelector(PAGE_SELECTOR);
        syncResizeTarget(page);
        if (!page) { return; }
        if (page.classList.contains(FALLBACK_CLASS)) {
            page.classList.remove(FALLBACK_CLASS);
        }
        if (hasStructuralClip(container)) {
            page.classList.add(FALLBACK_CLASS);
        }
    }

    /**
     * rAF 去抖：同一帧内的 resize / 尺寸变化 / DOM 变化合并为一次量测。
     */
    function schedule() {
        if (scheduled) { return; }
        scheduled = true;
        window.requestAnimationFrame(function () {
            scheduled = false;
            evaluate();
        });
    }

    function start() {
        var container = document.querySelector(CONTAINER_SELECTOR);
        if (!container) { return; }

        if (window.ResizeObserver) {
            resizeObserver = new ResizeObserver(schedule);
            resizeObserver.observe(container);
        }
        // 只监听 childList：兜底类名挂在 #page-app 上，监听属性会与自身切换互相触发
        if (window.MutationObserver) {
            mutationObserver = new MutationObserver(schedule);
            mutationObserver.observe(container, { childList: true, subtree: true });
        }
        window.addEventListener('resize', schedule);
        evaluate();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
