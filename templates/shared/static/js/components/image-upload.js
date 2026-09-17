/**
 * 图片上传组件 image-upload — 全局可复用（所有图片上传统一此样式）
 *
 * 布局对标：左侧虚线「上传图片 +」触发框 + 右侧图片预览框 + 下方尺寸/大小提示。
 * 用法（任意页面模板内直接使用，v-model 双向绑定图片 URL）：
 *   <image-upload v-model="basicForm.site_logo"
 *                 hint="尺寸: 宽130px, 高28px; 大小: ≤2M" :max-size="2"></image-upload>
 * 拖拽多图：传入数组 v-model，并设置 drag、multiple、max-count；批次完成后触发 upload-complete。
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
            modelValue: { type: [String, Array], default: '' },
            // 下方提示文字（尺寸/大小要求），为空则不显示
            hint: { type: String, default: '' },
            // 客户端大小限制（单位 MB），默认 2MB
            maxSize: { type: Number, default: 0 },
            uploadUrl: { type: String, default: UPLOAD_URL },
            fieldName: { type: String, default: 'file' },
            responseField: { type: String, default: 'url' },
            uploadPolicy: { type: Object, default: null },
            // 外部策略用于插件专用上传接口，组件只负责文件选择与结果回填。
            uploadStrategy: { type: Function, default: null },
            // 拖拽模式支持一次选择多张图片，并将 URL 数组回填给父组件。
            drag: { type: Boolean, default: false },
            multiple: { type: Boolean, default: false },
            maxCount: { type: Number, default: 1 },
            disabled: { type: Boolean, default: false },
            showSuccess: { type: Boolean, default: true }
        },
        emits: ['update:modelValue', 'upload-complete'],
        setup: function (props, ctx) {
            var uploading = Vue.ref(false);
            var isDragging = Vue.ref(false);
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
            var currentCount = Vue.computed(function () {
                if (Array.isArray(props.modelValue)) { return props.modelValue.length; }
                return props.modelValue ? 1 : 0;
            });
            var canUpload = Vue.computed(function () {
                var hasRoom = !props.multiple || currentCount.value < Math.max(1, props.maxCount);
                return policyReady.value && !uploading.value && !props.disabled && hasRoom;
            });
            var dropLabel = Vue.computed(function () {
                if (props.disabled) { return '暂不可上传'; }
                if (props.multiple && currentCount.value >= Math.max(1, props.maxCount)) {
                    return '已达到上传数量上限';
                }
                return '拖拽图片到此处，或点击选择';
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
                if (canUpload.value && inputRef.value) {
                    inputRef.value.click();
                }
            };

            var validateFile = function (file) {
                var extension = String(file.name || '').split('.').pop().toLowerCase();
                if (allowedExtensions.value.indexOf(extension) === -1) {
                    return '仅支持 ' + allowedExtensions.value.join('/') + ' 图片';
                }
                if (file.size > effectiveMaxSize.value * 1024 * 1024) {
                    return '图片大小不能超过 ' + effectiveMaxSize.value + 'MB';
                }
                return '';
            };

            var uploadFile = function (file) {
                var fd = new FormData();
                fd.append(props.fieldName, file);
                var uploadPromise = props.uploadStrategy
                    ? props.uploadStrategy(file, fd)
                    : request.post(props.uploadUrl, fd);
                return Promise.resolve(uploadPromise).then(function (res) {
                    var payload = res && res.data ? (res.data.data || res.data) : res;
                    var url = payload && (payload[props.responseField] || payload.url);
                    if (url) { return url; }
                    var message = res && res.data && res.data.msg;
                    throw new Error(message || '上传结果缺少图片地址');
                });
            };

            // 文件选择和拖放共用同一校验、上传与回填流程，多图按顺序上传。
            var uploadFiles = function (fileList) {
                var files = Array.prototype.slice.call(fileList || []);
                if (!canUpload.value || !files.length) { return Promise.resolve([]); }
                var room = props.multiple
                    ? Math.max(0, Math.max(1, props.maxCount) - currentCount.value)
                    : 1;
                if (!props.multiple) { files = files.slice(0, 1); }
                if (files.length > room) {
                    MessagePlugin.warning('本次最多还能上传 ' + room + ' 张图片');
                    files = files.slice(0, room);
                }
                files = files.filter(function (file) {
                    var error = validateFile(file);
                    if (error) { MessagePlugin.error(error); }
                    return !error;
                });
                if (!files.length) { return Promise.resolve([]); }

                uploading.value = true;
                var current = Array.isArray(props.modelValue) ? props.modelValue.slice() : [];
                var uploaded = [];
                var failures = 0;
                var chain = Promise.resolve();
                files.forEach(function (file) {
                    chain = chain.then(function () {
                        return uploadFile(file).then(function (url) {
                            if (current.indexOf(url) === -1 && uploaded.indexOf(url) === -1) {
                                uploaded.push(url);
                            }
                        }).catch(function () { failures += 1; });
                    });
                });
                return chain.then(function () {
                    if (uploaded.length) {
                        var nextValue = props.multiple
                            ? current.concat(uploaded).slice(0, Math.max(1, props.maxCount))
                            : uploaded[uploaded.length - 1];
                        ctx.emit('update:modelValue', nextValue);
                        ctx.emit('upload-complete', nextValue);
                        if (props.showSuccess) {
                            MessagePlugin.success(uploaded.length > 1
                                ? uploaded.length + ' 张图片上传成功'
                                : '上传成功');
                        }
                    }
                    if (failures) { MessagePlugin.error(failures + ' 张图片上传失败，请重试'); }
                    return uploaded;
                }).finally(function () {
                    uploading.value = false;
                    isDragging.value = false;
                });
            };

            var onFileChange = function (event) {
                uploadFiles(event.target.files).finally(function () { event.target.value = ''; });
            };
            var onDragOver = function () {
                if (canUpload.value) { isDragging.value = true; }
            };
            var onDragLeave = function () { isDragging.value = false; };
            var onDrop = function (event) {
                isDragging.value = false;
                uploadFiles(event.dataTransfer && event.dataTransfer.files);
            };

            // 删除已上传图片（清空绑定值）
            var clearImage = function () {
                ctx.emit('update:modelValue', '');
            };

            return {
                uploading: uploading,
                isDragging: isDragging,
                inputRef: inputRef,
                triggerPick: triggerPick,
                onFileChange: onFileChange,
                onDragOver: onDragOver,
                onDragLeave: onDragLeave,
                onDrop: onDrop,
                clearImage: clearImage,
                acceptValue: acceptValue,
                displayHint: displayHint,
                policyReady: policyReady,
                canUpload: canUpload,
                dropLabel: dropLabel,
                currentCount: currentCount
            };
        },
        template:
            '<div class="image-uploader" :class="{\'image-uploader--drag\': drag}">' +
            '  <div v-if="drag" class="image-uploader__dropzone" role="button"' +
            '    :tabindex="canUpload ? 0 : -1"' +
            '    :class="{\'is-dragging\': isDragging, \'is-loading\': uploading, \'is-disabled\': disabled, \'is-full\': multiple && currentCount >= maxCount}"' +
            '    @click="triggerPick" @keydown.enter.prevent="triggerPick" @keydown.space.prevent="triggerPick"' +
            '    @dragenter.prevent="onDragOver" @dragover.prevent="onDragOver" @dragleave.prevent="onDragLeave" @drop.prevent="onDrop">' +
            '    <t-loading v-if="uploading" text="上传中"></t-loading>' +
            '    <template v-else>' +
            '      <t-icon name="upload" class="image-uploader__drop-icon"></t-icon>' +
            '      <span class="image-uploader__drop-title">[[ dropLabel ]]</span>' +
            '      <span class="image-uploader__drop-hint">[[ displayHint ]]</span>' +
            '    </template>' +
            '  </div>' +
            '  <div v-else class="image-uploader__boxes">' +
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
            '  <div v-if="!drag" class="image-uploader__hint">[[ displayHint ]]</div>' +
            '  <input ref="inputRef" type="file" :accept="acceptValue" :multiple="multiple" :disabled="!canUpload"' +
            '    class="image-uploader__input" @change="onFileChange">' +
            '</div>'
    };

    // 暴露到全局组件注册表，由 hy-app.js 统一注册到每个页面应用
    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['image-upload'] = ImageUpload;
})(window);
