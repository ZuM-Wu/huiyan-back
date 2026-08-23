/**
 * AI 对话共享组件：ai-reasoning-block 思维链折叠块
 *
 * 复刻 TDesign Chat ChatReasoning 视觉：t-collapse + t-collapse-panel，
 * 头部未完成时显示 ai-chat-loading「思考中...」，完成后显示「已思考」。
 *
 * 依赖：Vue 3、TDesign Vue Next（t-collapse）、ai-chat-loading、ai-markdown-view
 * 注册：window.HuiYanComponents['ai-reasoning-block']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }
    window.HuiYanComponents = window.HuiYanComponents || {};
    var AiChatLoading = window.HuiYanComponents['ai-chat-loading'];
    var AiMarkdownView = window.HuiYanComponents['ai-markdown-view'];

    /* ===== AiReasoningBlock：思维链折叠块 ===== */
    var AiReasoningBlock = {
        name: 'AiReasoningBlock',
        props: {
            collapsed: { type: Boolean, default: false },
            done: { type: Boolean, default: false },
            data: { type: String, default: '' }
        },
        emits: ['update:collapsed'],
        components: { 'ai-chat-loading': AiChatLoading, 'ai-markdown-view': AiMarkdownView },
        setup: function (props, ctx) {
            // 适配 t-collapse 的 v-model:value（数组）到 collapsed（布尔）
            var collapseValue = Vue.computed({
                get: function () { return props.collapsed ? [] : ['r']; },
                set: function (val) { ctx.emit('update:collapsed', !val || val.length === 0); }
            });
            return { collapseValue: collapseValue };
        },
        template: [
            '<t-collapse v-model:value="collapseValue" class="t-chat__item__think">',
            '  <t-collapse-panel value="r" class="t-chat__item__think__content">',
            '    <template #header>',
            '      <ai-chat-loading v-if="!done" text="思考中..." />',
            '      <span v-else class="ai-reasoning-done-text">已思考</span>',
            '    </template>',
            '    <ai-markdown-view :content="data"></ai-markdown-view>',
            '  </t-collapse-panel>',
            '</t-collapse>'
        ].join('')
    };

    window.HuiYanComponents['ai-reasoning-block'] = AiReasoningBlock;
})(window);
