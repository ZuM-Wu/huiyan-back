/**
 * 上传设置面板：集中编辑核心与插件上传策略。
 * 该组件只在系统设置页加载，active=true 时才请求策略，避免无关 Tab 发起请求。
 */
(function (window) {
    'use strict';

    var IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp'];

    var UploadSettingsPanel = {
        name: 'UploadSettingsPanel',
        props: {
            active: { type: Boolean, default: false }
        },
        template: `
            <div class="settings-tab-content upload-settings-content">
                <t-alert v-if="warnings.length" theme="warning" class="mb16"
                    :message="warnings.join('；')"></t-alert>
                <t-alert theme="info" class="mb16"
                    message="所有上传入口按用途使用独立策略；插件配置中的上传字段仅作只读提示。保存后新策略优先于旧配置生效。"></t-alert>
                <t-skeleton v-if="loading" :row-col="[5]" animation="gradient"></t-skeleton>
                <template v-else>
                    <div v-for="group in groups" :key="group.key" class="upload-policy-group">
                        <div class="form-section-title mt0">[[ group.title ]]</div>
                        <div class="upload-policy-list">
                            <div v-for="policy in group.items" :key="policy.id" class="upload-policy-row"
                                :class="{'is-error': errorId === policy.id}" :data-policy-id="policy.id">
                                <div class="upload-policy-info">
                                    <div class="upload-policy-title">
                                        [[ policy.label ]]
                                        <t-tag v-if="policy.plugin_status === 1" theme="success"
                                            variant="light" size="small">已启用</t-tag>
                                        <t-tag v-else-if="policy.owner !== 'core'" theme="default"
                                            variant="light" size="small">未启用</t-tag>
                                    </div>
                                    <div class="upload-policy-desc">[[ policy.description ]]</div>
                                </div>
                                <t-input-number v-model="policy.max_size_mb" :min="1" :max="2048"
                                    suffix="MB"></t-input-number>
                                <div class="upload-policy-exts">
                                    <t-tag-input v-if="policy.extensions_editable" v-model="policy.extensions"
                                        placeholder="输入扩展名后回车" clearable></t-tag-input>
                                    <div v-else class="upload-policy-fixed">
                                        <t-tag v-for="ext in policy.extensions" :key="ext"
                                            variant="light">[[ ext ]]</t-tag>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-form-actions">
                        <t-button theme="primary" :loading="saving" @click="save">保存上传设置</t-button>
                    </div>
                </template>
            </div>
        `,
        setup: function (props) {
            var loading = Vue.ref(false);
            var saving = Vue.ref(false);
            var policies = Vue.ref([]);
            var warnings = Vue.ref([]);
            var errorId = Vue.ref('');
            var loaded = Vue.ref(false);
            var groups = Vue.computed(function () {
                return [
                    {
                        key: 'core', title: '通用上传',
                        items: policies.value.filter(function (item) { return item.owner === 'core'; })
                    },
                    {
                        key: 'plugin', title: '插件上传',
                        items: policies.value.filter(function (item) { return item.owner !== 'core'; })
                    }
                ].filter(function (group) { return group.items.length; });
            });

            function replacePolicies(data) {
                policies.value = (data.policies || []).map(function (item) {
                    return Object.assign({}, item, { extensions: (item.extensions || []).slice() });
                });
                warnings.value = data.warnings || [];
            }

            function fetchSettings() {
                if (loading.value || loaded.value) { return Promise.resolve(); }
                loading.value = true;
                return request.get('/upload/settings').then(function (res) {
                    replacePolicies((res.data && res.data.data) || res.data || {});
                    loaded.value = true;
                }).catch(function () {
                    TDesign.MessagePlugin.error('获取上传设置失败');
                }).finally(function () { loading.value = false; });
            }

            function normalizePolicy(policy) {
                policy.extensions = (policy.extensions || []).map(function (item) {
                    return String(item || '').trim().toLowerCase().replace(/^\.+/, '');
                }).filter(function (item, index, items) {
                    return item && items.indexOf(item) === index;
                });
            }

            function invalidPolicy() {
                return policies.value.find(function (policy) {
                    normalizePolicy(policy);
                    var size = Number(policy.max_size_mb);
                    var invalidExtension = policy.extensions.some(function (extension) {
                        return !/^[a-z0-9]{1,16}$/.test(extension);
                    });
                    var invalidImage = policy.id === 'core.image'
                        && policy.extensions.some(function (extension) {
                            return IMAGE_EXTENSIONS.indexOf(extension) === -1;
                        });
                    return !Number.isInteger(size) || size < 1 || size > 2048
                        || !policy.extensions.length || policy.extensions.length > 50
                        || invalidExtension || invalidImage;
                });
            }

            function focusError(policy) {
                errorId.value = policy ? policy.id : '';
                if (!policy) { return; }
                Vue.nextTick(function () {
                    var selector = '[data-policy-id="' + policy.id + '"]';
                    var row = document.querySelector(selector);
                    if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
                });
            }

            function save() {
                var invalid = invalidPolicy();
                if (invalid) {
                    focusError(invalid);
                    TDesign.MessagePlugin.error('请检查上传大小和允许格式');
                    return;
                }
                saving.value = true;
                request.put('/upload/settings', {
                    policies: policies.value.map(function (item) {
                        return {
                            id: item.id,
                            max_size_mb: Number(item.max_size_mb),
                            extensions: item.extensions
                        };
                    })
                }).then(function (res) {
                    replacePolicies((res.data && res.data.data) || res.data || {});
                    if (window.UploadPolicyClient && window.UploadPolicyClient.invalidate) {
                        window.UploadPolicyClient.invalidate();
                    }
                    focusError(null);
                    TDesign.MessagePlugin.success('上传设置已保存');
                }).catch(function () {
                    // 错误消息由 request.js 统一展示，保留当前输入供管理员修正。
                }).finally(function () { saving.value = false; });
            }

            Vue.watch(function () { return props.active; }, function (active) {
                if (active) { fetchSettings(); }
            }, { immediate: true });

            return {
                loading: loading,
                saving: saving,
                warnings: warnings,
                groups: groups,
                errorId: errorId,
                save: save
            };
        }
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['upload-settings-panel'] = UploadSettingsPanel;
})(window);
