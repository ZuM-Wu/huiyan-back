/**
 * 站内信共享组件：安全富文本阅读容器。
 *
 * 管理员历史消息可能保留 HTML；组件在浏览器端移除脚本、事件与危险 URL，
 * 并仅保留阅读场景需要的常见富文本标签和属性，纯文本则按原换行展示。
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }

    var ALLOWED_TAGS = {
        a: true, b: true, blockquote: true, br: true, code: true, del: true,
        div: true, em: true, h1: true, h2: true, h3: true, h4: true, h5: true,
        h6: true, hr: true, img: true, li: true, ol: true, p: true, pre: true,
        s: true, span: true, strong: true, table: true, tbody: true, td: true,
        th: true, thead: true, tr: true, u: true, ul: true
    };
    var ALLOWED_ATTRIBUTES = {
        a: { href: true, target: true, title: true },
        img: { alt: true, height: true, src: true, title: true, width: true },
        td: { colspan: true, rowspan: true },
        th: { colspan: true, rowspan: true }
    };

    function escapeText(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/\r?\n/g, '<br>');
    }

    function isSafeLink(value) {
        return /^(https?:|mailto:|tel:|\/|#)/i.test(value || '');
    }

    function isSafeImage(value) {
        return /^(https?:|\/|data:image\/(png|gif|jpe?g|webp);base64,)/i.test(value || '');
    }

    function sanitizeHtml(source) {
        var template = document.createElement('template');
        template.innerHTML = source;
        var forbidden = template.content.querySelectorAll('script, style, iframe, object, embed, form, svg, math');
        forbidden.forEach(function (element) { element.remove(); });

        Array.prototype.slice.call(template.content.querySelectorAll('*')).forEach(function (element) {
            var tag = element.tagName.toLowerCase();
            if (!ALLOWED_TAGS[tag]) {
                element.replaceWith.apply(element, Array.prototype.slice.call(element.childNodes));
                return;
            }
            Array.prototype.slice.call(element.attributes).forEach(function (attribute) {
                var name = attribute.name.toLowerCase();
                var allowed = ALLOWED_ATTRIBUTES[tag] && ALLOWED_ATTRIBUTES[tag][name];
                var value = attribute.value.trim();
                if (!allowed || (name === 'href' && !isSafeLink(value)) ||
                    (name === 'src' && !isSafeImage(value))) {
                    element.removeAttribute(attribute.name);
                }
            });
            if (tag === 'a' && element.getAttribute('target') === '_blank') {
                element.setAttribute('rel', 'noopener noreferrer');
            }
        });
        return template.innerHTML;
    }

    var InboxRichContent = {
        name: 'InboxRichContent',
        props: { content: { type: String, default: '' } },
        setup: function (props) {
            var html = Vue.computed(function () {
                var source = props.content || '';
                return /<\/?[a-z][\s\S]*>/i.test(source) ? sanitizeHtml(source) : escapeText(source);
            });
            return { html: html };
        },
        template: '<article class="inbox-rich-content" v-html="html"></article>'
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['inbox-rich-content'] = InboxRichContent;
})(window);
