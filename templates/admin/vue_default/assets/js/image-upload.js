/**
 * 图片上传组件 image-upload — 全局可复用（所有图片上传统一此样式）
 *
 * 布局对标：左侧虚线「上传图片 +」触发框 + 右侧图片预览框 + 下方尺寸/大小提示。
 * 用法（任意页面模板内直接使用，v-model 双向绑定图片 URL）：
 *   <image-upload v-model="basicForm.site_logo"
 *                 hint="尺寸: 宽130px, 高28px; 大小: ≤2M" :max-size="2"></image-upload>
 *
 * 依赖：
 * - 上传接口: POST /api/admin/v1/upload/image（返回 {status, msg, data: {url}}），用 request.js 携带 JWT
 * - 组件由 hy-app.js 的 createPage 在每个页面 Vue 应用上统一 app.component 注册
 * - 模板插值分隔符沿用应用级 [[ ]]（app.config.compilerOptions.delimiters）
 */
(function (window) {
    'use strict';

    // 上传接口地址（绝对路径，避免插件深层路由下相对解析错误）
    var UPLOAD_URL = '/upload/image';

    var ImageUpload = {
        name: 'ImageUpload',
        props: {
            // 图片 URL（v-model 绑定）
            modelValue: { type: String, default: '' },
            // 下方提示文字（尺寸/大小要求），为空则不显示
            hint: { type: String, default: '' },
            // 客户端大小限制（单位 MB），默认 2MB
            maxSize: { type: Number, default: 0 },
            uploadUrl: { type: String, default: UPLOAD_URL },
            fieldName: { type: String, default: 'file' },
            responseField: { type: String, default: 'url' },
            uploadPolicy: { type: Object, default: null },
            // 外部策略用于插件专用上传接口，组件只负责文件选择与结果回填。
            uploadStrategy: { type: Function, default: null }
        },
        emits: ['update:modelValue'],
        setup: function (props, ctx) {
            var uploading = Vue.ref(false);
            var inputRef = Vue.ref(null);
            var allowedExtensions = Vue.ref([]);
            var policyMaxSize = Vue.ref(0);
            var policyReady = Vue.ref(false);
            var policyError = Vue.ref(false);
            var MessagePlugin = TDesign.MessagePlugin;
            var effectiveMaxSize = Vue.computed(function () {
                return props.maxSize > 0 ? props.maxSize : policyMaxSize.value;
            });
            var acceptValue = Vue.computed(function () {
                return window.UploadPolicyClient.toAccept(allowedExtensions.value);
            });
            var displayHint = Vue.computed(function () {
                if (policyError.value) { return '上传规则加载失败，请刷新重试'; }
                var policyHint = '格式：' + allowedExtensions.value.join('/')
                    + '；大小 ≤' + effectiveMaxSize.value + 'MB';
                return props.hint ? props.hint + '；' + policyHint : policyHint;
            });
            var applyPolicy = function (policy) {
                if (!policy || !Array.isArray(policy.extensions) || !policy.extensions.length) {
                    return false;
                }
                allowedExtensions.value = policy.extensions;
                policyMaxSize.value = policy.max_size_mb || 0;
                policyReady.value = policyMaxSize.value > 0;
                return policyReady.value;
            };
            Vue.watch(function () { return props.uploadPolicy; }, applyPolicy, { deep: true });
            Vue.onMounted(function () {
                if (applyPolicy(props.uploadPolicy)) { return; }
                window.UploadPolicyClient.getImageLimits().then(function (limits) {
                    allowedExtensions.value = limits.image_extensions;
                    policyMaxSize.value = limits.image_max_size_mb;
                    policyReady.value = true;
                }).catch(function () {
                    policyError.value = true;
                    MessagePlugin.error('获取上传规则失败，请刷新重试');
                });
            });

            // 点击触发框 → 打开文件选择
            var triggerPick = function () {
                if (policyReady.value && !uploading.value && inputRef.value) {
                    inputRef.value.click();
                }
            };

            // 选中文件 → 前端校验 + 上传
            var onFileChange = function (e) {
                var file = e.target.files && e.target.files[0];
                if (!file) { return; }
                // 扩展名校验
                var extension = String(file.name || '').split('.').pop().toLowerCase();
                if (allowedExtensions.value.indexOf(extension) === -1) {
                    MessagePlugin.error('仅支持 ' + allowedExtensions.value.join('/') + ' 图片');
                    e.target.value = '';
                    return;
                }
                // 大小校验
                if (file.size > effectiveMaxSize.value * 1024 * 1024) {
                    MessagePlugin.error('图片大小不能超过 ' + effectiveMaxSize.value + 'MB');
                    e.target.value = '';
                    return;
                }
                uploading.value = true;
                var fd = new FormData();
                fd.append(props.fieldName, file);
                var uploadPromise = props.uploadStrategy
                    ? props.uploadStrategy(file, fd)
                    : request.post(props.uploadUrl, fd);
                uploadPromise.then(function (res) {
                    var payload = res && res.data ? (res.data.data || res.data) : res;
                    var url = payload && (payload[props.responseField] || payload.url);
                    if (url) {
                        ctx.emit('update:modelValue', url);
                        MessagePlugin.success('上传成功');
                    } else {
                        MessagePlugin.error(res.data.msg || '上传失败');
                    }
                }).catch(function () {
                    MessagePlugin.error('上传失败，请重试');
                }).finally(function () {
                    uploading.value = false;
                    e.target.value = '';
                });
            };

            // 删除已上传图片（清空绑定值）
            var clearImage = function () {
                ctx.emit('update:modelValue', '');
            };

            return {
                uploading: uploading,
                inputRef: inputRef,
                triggerPick: triggerPick,
                onFileChange: onFileChange,
                clearImage: clearImage,
                acceptValue: acceptValue,
                displayHint: displayHint,
                policyReady: policyReady
            };
        },
        template:
            '<div class="image-uploader">' +
            '  <div class="image-uploader__boxes">' +
            '    <div class="image-uploader__trigger" :class="{\'is-loading\': uploading}" @click="triggerPick">' +
            '      <t-loading v-if="uploading"></t-loading>' +
            '      <template v-else>' +
            '        <t-icon name="add" class="image-uploader__plus"></t-icon>' +
            '        <span class="image-uploader__label">上传图片</span>' +
            '      </template>' +
            '    </div>' +
            '    <div v-if="modelValue" class="image-uploader__preview">' +
            '      <img :src="modelValue" alt="预览" class="image-uploader__img">' +
            '      <div class="image-uploader__mask" title="删除图片" @click.stop="clearImage">' +
            '        <t-icon name="delete"></t-icon>' +
            '      </div>' +
            '    </div>' +
            '  </div>' +
            '  <div class="image-uploader__hint">[[ displayHint ]]</div>' +
            '  <input ref="inputRef" type="file" :accept="acceptValue" :disabled="!policyReady"' +
            '    class="image-uploader__input" @change="onFileChange">' +
            '</div>'
    };

    // 暴露到全局组件注册表，由 hy-app.js 统一注册到每个页面应用
    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['image-upload'] = ImageUpload;
})(window);
