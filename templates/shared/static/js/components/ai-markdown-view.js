/**
 * AI 对话共享组件：ai-markdown-view markdown 渲染容器
 *
 * 基于 marked.js + 简单 sanitize + v-html，用于思维链正文与工具结果的渲染。
 * AI 回复来自受控后端 LLM，非用户直接输入，简单 sanitize 足够。
 *
 * 依赖：Vue 3、marked.js（全局 window.marked，缺失时降级为纯文本）
 * 注册：window.HuiYanComponents['ai-markdown-view']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }

    // marked 在 AI 对话页 scripts 块中按需加载（base.html 加载本组件时尚未存在），
    // 因此首次渲染时惰性获取并一次性完成 setOptions 配置
    var marked = null;
    function ensureMarked() {
        if (!marked && window.marked) {
            marked = window.marked;
            // 配置 marked（breaks: 换行转 <br>，gfm: GitHub 风格 markdown）
            if (marked.setOptions) {
                marked.setOptions({ breaks: true, gfm: true, mangle: false, headerIds: false });
            }
        }
        return marked;
    }

    /**
     * 简单 HTML 清理：移除 <script>/<iframe> 标签和 on* 事件属性
     */
    function sanitizeHtml(html) {
        if (!html) { return ''; }
        return html
            .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
            .replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '')
            .replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
            .replace(/\son\w+\s*=\s*'[^']*'/gi, '')
            .replace(/\son\w+\s*=\s*[^\s>]+/gi, '');
    }

    /** markdown → HTML（含 sanitize） */
    function renderMarkdown(text) {
        if (!text) { return ''; }
        var m = ensureMarked();
        var html = m ? m.parse(text) : text;
        return sanitizeHtml(html);
    }

    /* ===== AiMarkdownView：markdown 渲染容器 ===== */
    var AiMarkdownView = {
        name: 'AiMarkdownView',
        props: {
            content: { type: String, default: '' },
            role: { type: String, default: 'assistant' }
        },
        setup: function (props) {
            var html = Vue.computed(function () { return renderMarkdown(props.content); });
            return { html: html };
        },
        template: '<div :class="[\'ai-markdown-body\', \'t-chat__text__\' + role]" v-html="html"></div>'
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['ai-markdown-view'] = AiMarkdownView;
})(window);
