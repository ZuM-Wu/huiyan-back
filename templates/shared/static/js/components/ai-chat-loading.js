/**
 * AI 对话共享组件：ai-chat-loading 三点移动动画
 *
 * 基于 TDesign Chat 标准组件结构中的 ChatLoading 视觉样式（CSS 手动渲染，不在 IIFE 中），
 * 用于消息流「AI 正在思考...」占位与思维链折叠块头部加载态。
 *
 * 依赖：Vue 3
 * 注册：window.HuiYanComponents['ai-chat-loading']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }

    /* ===== AiChatLoading：三点移动动画 ===== */
    var AiChatLoading = {
        name: 'AiChatLoading',
        props: { text: { type: String, default: '' } },
        template: [
            '<div class="t-chat-loading">',
            '  <div class="t-chat-loading__moving">',
            '    <span class="t-chat-loading__moving--top"></span>',
            '    <span class="t-chat-loading__moving--left"></span>',
            '    <span class="t-chat-loading__moving--right"></span>',
            '  </div>',
            '  <span v-if="text" class="t-chat-loading__text">[[ text ]]</span>',
            '</div>'
        ].join('')
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['ai-chat-loading'] = AiChatLoading;
})(window);
