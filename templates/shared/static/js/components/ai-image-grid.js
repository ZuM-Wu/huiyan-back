/**
 * AI 对话共享图片网格：输入预览与用户消息附件统一使用。
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }

    var AiImageGrid = {
        name: 'AiImageGrid',
        props: {
            items: { type: Array, default: function () { return []; } },
            removable: { type: Boolean, default: false }
        },
        emits: ['remove'],
        setup: function (props) {
            var viewerVisible = Vue.ref(false);
            var viewerIndex = Vue.ref(0);
            var viewerImages = Vue.computed(function () {
                return (props.items || []).map(function (item) {
                    return item.preview_url || item.url || '';
                }).filter(Boolean);
            });
            function openViewer(index) {
                if (!viewerImages.value[index]) { return; }
                viewerIndex.value = index;
                viewerVisible.value = true;
            }
            return {
                viewerVisible: viewerVisible, viewerIndex: viewerIndex,
                viewerImages: viewerImages, openViewer: openViewer
            };
        },
        template: [
            '<div v-if="items.length" class="ai-image-grid">',
            '  <div v-for="(image, index) in items" :key="image.key || image.url || index" class="ai-image-grid__item">',
            '    <t-image class="ai-image-grid__image" fit="cover"',
            '      :src="image.preview_url || image.url" :alt="image.name || \'聊天图片\'"',
            '      @click="openViewer(index)" />',
            '    <t-loading v-if="image.uploading" class="ai-image-grid__loading" size="small" />',
            '    <t-tooltip v-if="removable" content="移除图片" placement="top">',
            '      <t-button class="ai-image-grid__remove" theme="danger" variant="base"',
            '        shape="circle" size="small" aria-label="移除图片" @click.stop="$emit(\'remove\', index)">',
            '        <template #icon><t-icon name="close"></t-icon></template>',
            '      </t-button>',
            '    </t-tooltip>',
            '  </div>',
            '  <t-image-viewer v-model:visible="viewerVisible" :images="viewerImages" :index="viewerIndex" />',
            '</div>'
        ].join('')
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['ai-image-grid'] = AiImageGrid;
})(window);
