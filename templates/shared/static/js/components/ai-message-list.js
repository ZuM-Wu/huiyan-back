/**
 * AI 对话共享组件：ai-message-list 消息列表容器
 *
 * 基于 TDesign Chat TChat 组件：消息流容器 + footer slot 透传 + 思考中占位。
 * 暴露 scrollToBottom 方法（访问 TChat 内部 .t-chat__list 滚动元素），
 * 页面通过 ref 调用以配合 AiChatUtils.scrollToBottomRaf 节流。
 *
 * 依赖：Vue 3、TDesign Chat（t-chat）、ai-message-bubble、ai-chat-loading
 * 注册：window.HuiYanComponents['ai-message-list']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }
    window.HuiYanComponents = window.HuiYanComponents || {};
    var AiMessageBubble = window.HuiYanComponents['ai-message-bubble'];
    var AiChatLoading = window.HuiYanComponents['ai-chat-loading'];

    /* ===== AiMessageList：消息列表容器 ===== */
    var AiMessageList = {
        name: 'AiMessageList',
        props: {
            messages: { type: Array, default: function () { return []; } },
            loading: { type: Boolean, default: false }
        },
        components: { 'ai-message-bubble': AiMessageBubble, 'ai-chat-loading': AiChatLoading },
        setup: function (props, ctx) {
            var chatRef = Vue.ref(null);
            // 暴露 scrollToBottom 方法：访问 TChat 内部 .t-chat__list 滚动元素
            ctx.expose({
                scrollToBottom: function () {
                    var el = chatRef.value;
                    if (el && el.$el) {
                        var list = el.$el.querySelector('.t-chat__list');
                        if (list) { list.scrollTop = list.scrollHeight; }
                    }
                }
            });
            return { chatRef: chatRef };
        },
        template: [
            '<t-chat ref="chatRef" :text-loading="loading" auto-scroll :clear-history="false" :show-scroll-button="false">',
            '  <ai-message-bubble v-for="(msg, i) in messages" :key="i" :item="msg"></ai-message-bubble>',
            '  <div v-if="loading" class="ai-msg-loading">',
            '    <ai-chat-loading text="AI 正在思考..." />',
            '  </div>',
            '  <template #footer><slot name="footer"></slot></template>',
            '</t-chat>'
        ].join('')
    };

    window.HuiYanComponents['ai-message-list'] = AiMessageList;
})(window);
