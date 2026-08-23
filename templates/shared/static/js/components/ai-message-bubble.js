/**
 * AI 对话共享组件：ai-message-bubble 单条消息气泡
 *
 * 基于 TDesign Chat TChatItem：#content slot 自渲染推理链/正文/工具气泡。
 * slotFirst:true 使 ChatItem 跳过内部 ChatContent（cherry-markdown）；
 * 不传 :reasoning prop（默认 false）使 ChatItem 跳过内部 ChatReasoning（无自动折叠）。
 *
 * 依赖：Vue 3、TDesign Chat（t-chat-item）、ai-tool-bubble、ai-reasoning-block、ai-markdown-view
 * 注册：window.HuiYanComponents['ai-message-bubble']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }
    window.HuiYanComponents = window.HuiYanComponents || {};
    var AiToolBubble = window.HuiYanComponents['ai-tool-bubble'];
    var AiReasoningBlock = window.HuiYanComponents['ai-reasoning-block'];
    var AiMarkdownView = window.HuiYanComponents['ai-markdown-view'];
    var AiImageGrid = window.HuiYanComponents['ai-image-grid'];

    /* ===== AiMessageBubble：单条消息气泡 ===== */
    var AiMessageBubble = {
        name: 'AiMessageBubble',
        props: { item: { type: Object, required: true } },
        components: {
            'ai-tool-bubble': AiToolBubble,
            'ai-reasoning-block': AiReasoningBlock,
            'ai-markdown-view': AiMarkdownView,
            'ai-image-grid': AiImageGrid
        },
        // 不需要 setup — 模板直接遍历 item.content 段数组，按 seg.type 分发到对应子组件
        template: [
            '<t-chat-item :role="item.role" :variant="item.role === \'user\' ? \'base\' : \'text\'"',
            '  :name="item.name" :datetime="item.datetime">',
            '  <template #content>',
            '    <template v-for="(seg, i) in (item.content || [])" :key="i">',
            '      <ai-image-grid v-if="seg.type === \'images\'" :items="seg.items || []" />',
            '      <ai-reasoning-block v-if="seg.type === \'reasoning\'"',
            '        :collapsed="seg.collapsed" :done="seg.done" :data="seg.data || \'\'"',
            '        @update:collapsed="seg.collapsed = $event" />',
            '      <ai-markdown-view v-else-if="seg.type === \'markdown\'"',
            '        :content="seg.data || \'\'" :role="item.role" />',
            '      <ai-tool-bubble v-else-if="seg.type === \'tool\'"',
            '        :seg="seg" />',
            '    </template>',
            '  </template>',
            '</t-chat-item>'
        ].join('')
    };

    window.HuiYanComponents['ai-message-bubble'] = AiMessageBubble;
})(window);
