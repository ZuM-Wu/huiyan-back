/**
 * 实名认证管理页面脚本 — 对应路由 /admin/certification
 * 三个 Tab：实名审批、实名设置、接口管理
 * Composition API + ES6 + HuiYan.createPage
 */
(function () {
    'use strict';

    const { ref, reactive, onMounted, watch } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            const activeTab = ref(HuiYan.getUrlTab('review', ['review', 'config', 'channel']));
            watch(activeTab, (val) => { HuiYan.syncUrlTab(val); });

            /* ========== 实名审批 ========== */
            const recordList = ref([]);
            const recordLoading = ref(false);
            const searchKeyword = ref('');
            const filterStatus = ref(null);
            const recordPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const recordColumns = [
                { colKey: 'id', title: 'ID', width: 70 },
                { colKey: 'farmer_name', title: '申请人', width: 140, ellipsis: true },
                { colKey: 'cert_type', title: '类型', width: 80, cell: 'cert_type' },
                { colKey: 'status', title: '状态', width: 100, cell: 'status' },
                { colKey: 'submit_time', title: '提交时间', width: 170 },
                { colKey: 'operation', title: '操作', width: 130, cell: 'operation', fixed: 'right' },
            ];
            const reviewDialogVisible = ref(false);
            const reviewRecord = ref(null);
            const reviewRemark = ref('');
            const reviewLoading = ref(false);
            const detailDialogVisible = ref(false);
            const detailRecord = ref(null);
            
                        // 认证渠道英文标识 → 中文名称映射
                        const channelLabel = (channel) => {
                            const map = {
                                zhima_credit: '芝麻信用实名认证',
                                manual: '人工审核'
                            };
                            return map[channel] || channel || '--';
                        };

            const fetchRecords = () => {
                recordLoading.value = true;
                var params = {
                    page: recordPagination.current,
                    limit: recordPagination.pageSize,
                    keywords: searchKeyword.value,
                };
                if (filterStatus.value !== null) {
                    params.status = filterStatus.value;
                }
                request.get('/certification/record/list', { params: params }).then((res) => {
                    var data = request.unwrapData(res) || {};
                    recordList.value = data.list || [];
                    recordPagination.total = data.total || 0;
                }).catch(() => {}).finally(() => {
                    recordLoading.value = false;
                });
            };

            const onRecordPageChange = (pageInfo) => {
                recordPagination.current = pageInfo.current;
                recordPagination.pageSize = pageInfo.pageSize;
                fetchRecords();
            };

            const openReview = (row) => {
                reviewRecord.value = row;
                reviewRemark.value = '';
                reviewDialogVisible.value = true;
            };

            const openDetail = (row) => {
                request.get('/certification/record/' + row.id).then((res) => {
                    detailRecord.value = res.data.data || res.data;
                    detailDialogVisible.value = true;
                }).catch(() => {
                    MessagePlugin.error('获取详情失败');
                });
            };

            const doReview = (status) => {
                if (!reviewRecord.value) return;
                reviewLoading.value = true;
                request.put('/certification/record/' + reviewRecord.value.id + '/review', {
                    status: status,
                    review_remark: reviewRemark.value
                }).then((res) => {
                    MessagePlugin.success(res.data.msg || '审批完成');
                    reviewDialogVisible.value = false;
                    fetchRecords();
                }).catch(() => {}).finally(() => {
                    reviewLoading.value = false;
                });
            };

            /* ========== 实名设置 ========== */
            const configItems = [
                { key: 'cert_enabled', label: '实名认证', desc: '开启后用户需进行实名认证' },
                { key: 'cert_auto_update_name', label: '自动更新姓名', desc: '实名通过后，将自动更新昵称为实名名称' },
                { key: 'cert_show_id', label: '展示认证ID', desc: '农户端个人中心展示实名认证ID' },
                { key: 'cert_manual_review', label: '人工复审', desc: '第三方认证通过后，需要后台人工审批通过' },
                { key: 'cert_notify_user', label: '审批通过后通知用户', desc: '后台审批通过后，通知用户认证结果' },
                { key: 'cert_upload_image', label: '上传图片', desc: '提交认证资料时，需要上传身份证图片' },
                { key: 'cert_phone_match', label: '手机一致性', desc: '注册手机号需与实名手机号一致才可提交认证' },
            ];
            const configForm = reactive({});
            const configSaving = ref(false);

            const fetchConfig = () => {
                request.get('/certification/config').then((res) => {
                    var data = res.data.data || res.data;
                    Object.assign(configForm, data);
                }).catch(() => {
                    MessagePlugin.error('加载配置失败');
                });
            };

            const saveConfig = () => {
                configSaving.value = true;
                request.put('/certification/config', configForm).then((res) => {
                    MessagePlugin.success(res.data.msg || '配置已保存');
                }).catch(() => {}).finally(() => {
                    configSaving.value = false;
                });
            };

            /* ========== 接口管理（列表 + 编辑弹窗） ========== */
            const pluginList = ref([]);
            const pluginLoading = ref(false);
            const pluginColumns = [
                { colKey: 'title', title: '接口名称', width: 200 },
                { colKey: 'name', title: '插件标识', width: 160, ellipsis: true },
                { colKey: 'version', title: '版本', width: 80 },
                { colKey: 'status', title: '状态', width: 100, cell: 'status' },
                { colKey: 'operation', title: '操作', width: 200, cell: 'operation', fixed: 'right' },
            ];
            const pluginDialogVisible = ref(false);
            const pluginEditTitle = ref('');
            const pluginEditSchema = ref([]);
            const pluginForm = reactive({});
            const pluginSaving = ref(false);
            var pluginEditName = '';

            const fetchPlugins = () => {
                pluginLoading.value = true;
                request.get('/certification/channel/plugins').then((res) => {
                    pluginList.value = res.data.data.list || [];
                }).catch(() => {}).finally(() => {
                    pluginLoading.value = false;
                });
            };

            const openPluginEdit = (row) => {
                pluginEditName = row.name;
                pluginEditTitle.value = (row.channel_id ? '编辑' : '添加') + ' - ' + row.title;
                pluginEditSchema.value = row.config_schema || [];
                Object.keys(pluginForm).forEach((k) => { delete pluginForm[k]; });
                Object.assign(pluginForm, row.config || {});
                pluginDialogVisible.value = true;
            };

            const doPluginSave = () => {
                pluginSaving.value = true;
                request.put('/certification/channel/plugin/' + pluginEditName, {
                    config: { ...pluginForm },
                    channel_name: pluginEditTitle.value.split(' - ').slice(1).join(' - '),
                    channel_type: 'personal',
                }).then((res) => {
                    MessagePlugin.success(res.data.msg || '配置已保存');
                    pluginDialogVisible.value = false;
                    fetchPlugins();
                }).catch(() => {}).finally(() => {
                    pluginSaving.value = false;
                });
            };

            const togglePlugin = (row) => {
                var newStatus = row.status === 1 ? 0 : 1;
                request.put('/certification/channel/plugin/' + row.name + '/status', {
                    status: newStatus
                }).then((res) => {
                    MessagePlugin.success(res.data.msg || '操作成功');
                    fetchPlugins();
                }).catch(() => {});
            };

            const uninstallPlugin = (row) => {
                var instance = DialogPlugin.confirm({
                    header: '确认卸载',
                    body: '确定要卸载 [' + row.title + '] 的配置吗？插件文件不会被删除。',
                    theme: 'danger',
                    onConfirm: () => {
                        request.delete('/certification/channel/plugin/' + row.name).then(() => {
                            MessagePlugin.success('已卸载');
                            instance.destroy();
                            fetchPlugins();
                        }).catch(() => { instance.destroy(); });
                    },
                    onClose: () => { instance.destroy(); }
                });
            };

            /* ========== Tab 切换懒加载（immediate 兼容 URL 直达非默认 Tab） ========== */
            watch(activeTab, (val) => {
                if (val === 'config' && Object.keys(configForm).length === 0) {
                    fetchConfig();
                }
                if (val === 'channel' && pluginList.value.length === 0) {
                    fetchPlugins();
                }
            }, { immediate: true });

            onMounted(() => {
                fetchRecords();
            });

            return {
                activeTab,
                recordList, recordLoading, searchKeyword, filterStatus,
                recordPagination, recordColumns, fetchRecords, onRecordPageChange,
                reviewDialogVisible, reviewRecord, reviewRemark, reviewLoading,
                openReview, openDetail, doReview,
                detailDialogVisible, detailRecord, channelLabel,
                configItems, configForm, configSaving, fetchConfig, saveConfig,
                pluginList, pluginLoading, pluginColumns,
                pluginDialogVisible, pluginEditTitle, pluginEditSchema,
                pluginForm, pluginSaving, fetchPlugins,
                openPluginEdit, doPluginSave, togglePlugin, uninstallPlugin,
            };
        }
    });
})();
