/**
 * AI 对话共享组件：ai-tool-bubble 工具调用过程气泡
 *
 * 自定义 CSS 展示工具调用过程（TDesign Chat 无对应组件）：
 * 工具名 + 执行状态标签（无权限/完成/执行中）+ 可展开的参数/结果。
 *
 * 依赖：Vue 3、TDesign Vue Next（t-icon/t-tag）
 * 注册：window.HuiYanComponents['ai-tool-bubble']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }
    var AiMarkdownView = window.HuiYanComponents['ai-markdown-view'];

    /* ===== AiToolBubble：工具调用过程气泡 ===== */
    var AiToolBubble = {
        name: 'AiToolBubble',
        props: { seg: { type: Object, required: true } },
        components: { 'ai-markdown-view': AiMarkdownView },
        template: [
            '<div :class="[\'ai-tool-bubble\', { executing: !seg.done, denied: seg.denied }]">',
            '  <div class="tool-name">',
            '    <t-icon name="tools"></t-icon> 调用工具：[[ seg.name ]]',
            '    <span v-if="!seg.done" class="tool-executing-dot"></span>',
            '    <t-tag v-if="seg.denied" theme="danger" variant="light" size="small">无权限</t-tag>',
            '    <t-tag v-else-if="seg.done" theme="success" variant="light" size="small">完成</t-tag>',
            '    <t-tag v-else theme="warning" variant="light" size="small">执行中</t-tag>',
            '    <span v-if="seg.arguments || (seg.done && seg.result)" class="tool-toggle"',
            '      @click="seg.expanded = !seg.expanded">[[ seg.expanded ? \'收起\' : \'展开\' ]]</span>',
            '  </div>',
            '  <div v-if="seg.expanded && seg.arguments" class="tool-args">参数：[[ seg.arguments ]]</div>',
            '  <div v-if="seg.expanded && seg.done"',
            '    :class="seg.denied ? \'tool-result tool-denied\' : \'tool-result\'">',
            '    <span v-if="seg.denied">[[ seg.result ]]</span>',
            '    <ai-markdown-view v-else :content="seg.result || \'\'" role="assistant" />',
            '  </div>',
            '</div>'
        ].join('')
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['ai-tool-bubble'] = AiToolBubble;
})(window);
