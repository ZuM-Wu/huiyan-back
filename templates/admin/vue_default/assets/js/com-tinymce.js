/**
 * 富文本编辑器组件 com-tinymce — 基于 TinyMCE 5.x
 *
 * 用法（在邮件模板编辑等需要富文本的场景中直接使用）:
 *   <com-tinymce ref="editor" :default-value="form.content" :height="400"></com-tinymce>
 *   // 获取内容: this.$refs.editor.getContent()
 *   // 设置内容: this.$refs.editor.setContent(html)
 *
 * 依赖:
 * - TinyMCE 静态资源: /static/vendor/tinymce/tinymce.min.js（需在页面中提前引入）
 * - 图片上传接口: POST /api/admin/v1/upload/image（返回 {status, msg, data: {url}}）
 * - 组件由 hy-app.js 的 createPage 在每个页面 Vue 应用上统一注册
 */
(function (window) {
    'use strict';

    var UPLOAD_URL = '/api/admin/v1/upload/image';

    var ComTinymce = {
        name: 'ComTinymce',
        template: '<textarea :id="id" name="content" :placeholder="prePlaceholder" :value="calStr" @input="onFallbackInput"></textarea>',
        props: {
            // 编辑器唯一 ID（同一页面多个编辑器需不同 ID）
            id: { type: String, default: 'tiny' },
            // 占位提示文字
            prePlaceholder: { type: String, default: '' },
            // 编辑器高度
            height: { type: Number, default: 400 },
            // 初始内容（HTML）
            defaultValue: { type: String, default: '' },
            // 是否只读
            readonly: { type: Boolean, default: false },
        },
        setup(props) {
            const { ref, computed, watch, onMounted, onBeforeUnmount, getCurrentInstance } = Vue;

            const content = ref(props.defaultValue || '');

            const calStr = computed(() => {
                var temp = content.value && content.value.replace(/&amp;/g, '');
                return temp;
            });

            watch(content, (val) => {
                if (window.tinymce && tinymce.editors[props.id]) {
                    tinymce.editors[props.id].setContent(val);
                }
            });

            /* 供父组件调用 — 获取富文本内容 */
            const getContent = () => {
                if (window.tinymce && tinymce.editors[props.id]) {
                    return tinymce.editors[props.id].getContent();
                }
                const instance = getCurrentInstance();
                const textarea = instance && instance.proxy && instance.proxy.$el;
                return textarea ? textarea.value : content.value;
            };

            /* 供父组件调用 — 设置富文本内容 */
            const setContent = (val) => {
                content.value = val;
            };

            /* TinyMCE 资源未加载时保留原生 textarea 的输入，避免正文被误判为空。 */
            const onFallbackInput = (event) => {
                content.value = event.target.value;
            };

            /* 轮询等待宿主元素可见后再 init（带 3 秒超时兑底，避免静默卡死） */
            const waitVisibleThenInit = () => {
                const instance = getCurrentInstance();
                var deadline = Date.now() + 3000;
                var check = function () {
                    var el = instance && instance.proxy && instance.proxy.$el;
                    // 组件已卸载（弹窗 destroy-on-close 快速开合）则放弃初始化
                    if (!el || !el.isConnected) { return; }
                    // offsetParent 非空说明元素及祖先均非 display:none，弹窗过渡完成
                    if (el.offsetParent !== null) { initEditor(); return; }
                    if (Date.now() > deadline) {
                        console.warn('[com-tinymce] 宿主元素 3 秒内未可见，强制初始化: #' + props.id);
                        initEditor();
                        return;
                    }
                    window.requestAnimationFrame(check);
                };
                window.requestAnimationFrame(check);
            };

            const initEditor = () => {
                if (!window.tinymce) {
                    console.warn('[com-tinymce] TinyMCE 资源未加载，使用原生 textarea 降级输入');
                    return;
                }
                // 弹窗快速开合可能遗留同 id 死实例，init 前先清理避免初始化冲突
                if (window.tinymce && window.tinymce.get(props.id)) {
                    window.tinymce.get(props.id).remove();
                }
                // TinyMCE 5 自动探测 baseURL 时扫描 script 标签，正则
                // /tinymce(\.full|\.jquery|)(\.min|\.dev|)\.js/ 会误命中本组件文件
                // com-tinymce.js，导致 theme/skin 请求指向 /static/js/components/ 而 404，
                // 此处显式修正为 vendor 目录
                window.tinymce.baseURL = '/static/vendor/tinymce';
                window.tinymce.suffix = '.min';
                window.tinymce.baseURI = new window.tinymce.util.URI('/static/vendor/tinymce');
                var curLang = 'zh_CN';
                var langKey = localStorage.getItem('backLang') || 'zh-cn';
                if (langKey === 'zh-hk') {
                    curLang = 'zh_HK';
                } else if (langKey === 'en-us') {
                    curLang = 'en_US';
                }

                window.tinymce.init({
                    selector: '#' + props.id,
                    language_url: '/static/vendor/tinymce/langs/' + curLang + '.js',
                    language: curLang,
                    min_height: props.height,
                    // 规范豁免：TinyMCE 编辑区为独立 iframe，无法继承主页 theme.css 的
                    // CSS 变量，此处注入的深色样式只能直写色值（含 rgba）
                    content_style: (
                        ":root[theme-mode='dark'] .mce-content-body {color: #fff;}" +
                        ":root[theme-mode='dark'] body#tinymce[data-mce-placeholder]::before {color: rgba(255,255,255,0.1);}"
                    ),
                    width: '100%',
                    plugins: 'link lists table colorpicker textcolor wordcount contextmenu paste fullscreen image code',
                    toolbar: 'bold italic underline strikethrough | fontsizeselect | forecolor backcolor | alignleft aligncenter alignright alignjustify | bullist numlist | outdent indent blockquote | undo redo | link unlink image code | removeformat | fullscreen',
                    images_upload_url: UPLOAD_URL,
                    readonly: props.readonly,
                    convert_urls: false,
                    deprecation_warnings: false,
                    end_container_on_empty_block: true,
                    paste_data_images: true,
                    forced_root_block: '',
                    images_upload_handler: function (blobInfo, success, failure) {
                        var formData = new FormData();
                        formData.append('file', blobInfo.blob());
                        request.post('/upload/image', formData).then(function (res) {
                            var url = res.data.data && res.data.data.url;
                            if (res.data.status === 200 && url) {
                                success(url);
                            } else {
                                failure(res.data.msg || '上传失败');
                            }
                        }).catch(function (err) {
                            failure('上传失败: ' + (err.message || '网络错误'));
                        });
                    },
                    init_instance_callback: function () {
                        if (content.value) {
                            if (tinymce.editors[props.id]) {
                                tinymce.editors[props.id].setContent(content.value);
                            }
                        }
                    },
                });
            };

            onMounted(() => {
                // 弹窗（t-dialog）内的编辑器在进场过渡期容器不可见，
                // TinyMCE 在隐藏元素上 init 会中断导致 visibility:hidden 永不解除，
                // 故等待宿主元素可见后再初始化（整页场景首帧即可见，行为不变）
                waitVisibleThenInit();
            });

            onBeforeUnmount(() => {
                // 防止富文本在弹窗中第二次渲染失败
                if (window.tinymce && tinymce.editors[props.id]) {
                    tinymce.editors[props.id].destroy();
                }
            });

            return { content, calStr, getContent, setContent, onFallbackInput, waitVisibleThenInit, initEditor };
        },
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['com-tinymce'] = ComTinymce;
})(window);
