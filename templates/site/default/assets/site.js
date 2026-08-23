/**
 * 官网默认主题脚本
 *
 * 纯展示交互：
 *   1. 移动端汉堡菜单展开 / 收起
 *   2. 平滑滚动到锚点（#features 等）
 *   3. 已登录管理员 → 将「登录」按钮改为「进入工作台」
 *   4. 主题模式切换（明亮/深色），持久化到 localStorage
 *
 * 不依赖 Vue / TDesign / Axios（官网公开页无需这些库）。
 */
(function () {
    'use strict';

    // ---------- 1. 移动端菜单 ---------- //
    var toggleBtn = document.getElementById('site-menu-toggle');
    var menu = document.querySelector('.site-menu');
    if (toggleBtn && menu) {
        toggleBtn.addEventListener('click', function () {
            var isOpen = menu.classList.toggle('is-open');
            toggleBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
        // 点击外部时收起
        document.addEventListener('click', function (e) {
            if (!menu.contains(e.target) && !toggleBtn.contains(e.target) && menu.classList.contains('is-open')) {
                menu.classList.remove('is-open');
            }
        });
    }

    // ---------- 2. 平滑滚动到锚点 ---------- //
    document.addEventListener('click', function (e) {
        var a = e.target.closest && e.target.closest('a[href^="#"]');
        if (!a) { return; }
        var id = a.getAttribute('href').slice(1);
        if (!id) { return; }
        var target = document.getElementById(id);
        if (!target) { return; }
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    // ---------- 3. 登录态检测（admin_token 存在则切换为「进入工作台」）---------- //
    var loginBtn = document.getElementById('site-login-btn');
    if (loginBtn) {
        try {
            var token = localStorage.getItem('admin_token');
            if (token) {
                loginBtn.textContent = '进入工作台';
                loginBtn.href = '/admin';
            }
        } catch (err) {
            /* localStorage 不可用时保持原「登录」文案 */
        }
    }

    // ---------- 4. 主题模式切换（明亮/深色）---------- //
    // 防闪烁脚本已在 <head> 中预设 data-theme 属性；此处仅处理按钮点击切换
    var themeToggle = document.getElementById('site-theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            var newTheme = isDark ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            try { localStorage.setItem('site_theme', newTheme); } catch (e) { /* 忽略 */ }
        });
    }
})();
