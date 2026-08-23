/**
 * AI 对话共享组件：ai-chat-sender 发送区
 *
 * 基于 TDesign Chat TChatSender：统一管理图片选择、预览与 footer 插槽。
 *
 * 依赖：Vue 3、TDesign Chat（t-chat-sender）
 * 注册：window.HuiYanComponents['ai-chat-sender']，由 hy-app.js / farmer-app.js 自动注册
 */
(function (window) {
    'use strict';

    var Vue = window.Vue;
    if (!Vue) { return; }

    /* ===== AiChatSender：发送区 ===== */
    var AiChatSender = {
        name: 'AiChatSender',
        props: {
            modelValue: { type: String, default: '' },
            loading: { type: Boolean, default: false },
            disabled: { type: Boolean, default: false },
            placeholder: { type: String, default: '输入消息，Enter 发送...' },
            attachments: { type: Array, default: function () { return []; } },
            maxAttachments: { type: Number, default: 4 }
        },
        emits: ['update:modelValue', 'update:attachments', 'send', 'stop'],
        components: {
            'ai-image-grid': window.HuiYanComponents['ai-image-grid']
        },
        setup: function (props, ctx) {
            var imageInputRef = Vue.ref(null);
            var allowedExtensions = Vue.ref([]);
            var imageMaxSizeMb = Vue.ref(0);
            var policyReady = Vue.ref(false);
            var policyError = Vue.ref(false);
            var acceptValue = Vue.computed(function () {
                return window.UploadPolicyClient.toAccept(allowedExtensions.value);
            });
            Vue.onMounted(function () {
                window.UploadPolicyClient.getImageLimits().then(function (limits) {
                    allowedExtensions.value = limits.image_extensions;
                    imageMaxSizeMb.value = limits.image_max_size_mb;
                    policyReady.value = true;
                }).catch(function () {
                    policyError.value = true;
                    if (window.TDesign) {
                        window.TDesign.MessagePlugin.error('获取图片上传规则失败，请刷新重试');
                    }
                });
            });
            // ChatSender 的 textareaProps 透传给内部 t-textarea
            var textareaProps = Vue.computed(function () {
                return { placeholder: props.placeholder, autosize: { minRows: 1, maxRows: 1 } };
            });
            function selectImages() {
                if (policyReady.value && !props.disabled && !props.loading && imageInputRef.value) {
                    imageInputRef.value.click();
                }
            }
            function onImagesSelected(event) {
                var selected = Array.prototype.slice.call(event.target.files || []);
                event.target.value = '';
                var available = Math.max(0, props.maxAttachments - props.attachments.length);
                if (selected.length > available && window.TDesign) {
                    window.TDesign.MessagePlugin.warning('每条消息最多上传 ' + props.maxAttachments + ' 张图片');
                }
                var additions = selected.slice(0, available).filter(function (file) {
                    var extension = String(file.name || '').split('.').pop().toLowerCase();
                    return allowedExtensions.value.indexOf(extension) !== -1
                        && file.size <= imageMaxSizeMb.value * 1024 * 1024;
                }).map(function (file) {
                    return {
                        key: file.name + '-' + file.size + '-' + file.lastModified,
                        file: file, name: file.name, mime_type: file.type, size: file.size,
                        preview_url: URL.createObjectURL(file)
                    };
                });
                if (additions.length < Math.min(selected.length, available) && window.TDesign) {
                    window.TDesign.MessagePlugin.error(
                        '图片格式或大小不符合上传设置（最大 ' + imageMaxSizeMb.value + 'MB）'
                    );
                }
                ctx.emit('update:attachments', props.attachments.concat(additions));
            }
            function removeImage(index) {
                var next = props.attachments.slice();
                var removed = next.splice(index, 1)[0];
                if (removed && String(removed.preview_url || '').indexOf('blob:') === 0) {
                    URL.revokeObjectURL(removed.preview_url);
                }
                ctx.emit('update:attachments', next);
            }
            Vue.watch(function () { return props.attachments; }, function (current, previous) {
                (previous || []).forEach(function (item) {
                    if ((current || []).indexOf(item) === -1
                            && String(item.preview_url || '').indexOf('blob:') === 0) {
                        URL.revokeObjectURL(item.preview_url);
                    }
                });
            });
            Vue.onBeforeUnmount(function () {
                (props.attachments || []).forEach(function (item) {
                    if (String(item.preview_url || '').indexOf('blob:') === 0) {
                        URL.revokeObjectURL(item.preview_url);
                    }
                });
            });
            return {
                textareaProps: textareaProps, imageInputRef: imageInputRef,
                selectImages: selectImages, onImagesSelected: onImagesSelected,
                removeImage: removeImage, acceptValue: acceptValue,
                policyReady: policyReady
            };
        },
        template: [
            '<div class="ai-chat-sender-shell">',
            '  <ai-image-grid :items="attachments" removable @remove="removeImage" />',
            '  <t-chat-sender class="chat-sender"',
            '  :model-value="modelValue"',
            '  @update:model-value="$emit(\'update:modelValue\', $event)"',
            '  :loading="loading"',
            '  :disabled="disabled"',
            '  :textarea-props="textareaProps"',
            '  @send="$emit(\'send\', $event)"',
            '  @stop="$emit(\'stop\')">',
            '  <template #footer-prefix>',
            '    <slot name="footer-prefix"></slot>',
            '  </template>',
            '  <template #suffix="slotProps">',
            '    <t-tooltip content="上传图片" placement="top">',
            '      <t-button theme="default" variant="text" shape="circle"',
                '        :disabled="disabled || loading || !policyReady || attachments.length >= maxAttachments"',
            '        aria-label="上传图片" @click="selectImages">',
            '        <template #icon><t-icon name="image"></t-icon></template>',
            '      </t-button>',
            '    </t-tooltip>',
            '    <slot name="suffix" v-bind="slotProps"></slot>',
            '  </template>',
            '  </t-chat-sender>',
            '  <input ref="imageInputRef" class="ai-image-input" type="file" multiple',
            '    :accept="acceptValue" @change="onImagesSelected">',
            '</div>'
        ].join('')
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['ai-chat-sender'] = AiChatSender;
})(window);
