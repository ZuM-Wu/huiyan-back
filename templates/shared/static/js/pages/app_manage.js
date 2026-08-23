/**
 * App 管理插件 — 后台管理页前端逻辑
 *
 * 由 app_manage.html 内联脚本等价外提（降低模板单文件行数）。
 * 脚本内唯一 Jinja2 变量 app_version 改由模板注入的 window.__PAGE_DATA__ 桥接。
 */
(function () {
    const { ref, reactive, watch, onMounted, computed } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'app_manage', page: 'app_manage',
        setup() {
            // 主 Tab 接入系统 URL 同步机制（hy-app.js 公共方法）：刷新/直达链接不丢 Tab
            const activeTab = ref(HuiYan.getUrlTab('version', ['version', 'ad', 'notice']));
            watch(activeTab, (val) => { HuiYan.syncUrlTab(val); });
            const apkPolicy = ref({ max_size_mb: 0, extensions: [] });
            const adImagePolicy = ref({ max_size_mb: 0, extensions: [] });
            const uploadPolicyReady = ref(false);
            const apkAccept = computed(() => apkPolicy.value.extensions.map(ext => '.' + ext).join(','));
            const adImageAccept = computed(() => adImagePolicy.value.extensions.map(ext => '.' + ext).join(','));
            const apkHint = computed(() => apkPolicy.value.extensions.join('/') + '，最大 ' + apkPolicy.value.max_size_mb + 'MB');
            const adImageHint = computed(() => adImagePolicy.value.extensions.join('/') + '，最大 ' + adImagePolicy.value.max_size_mb + 'MB');
            const fetchUploadPolicies = () => request.get('/upload/settings').then((res) => {
                const data = (res.data && res.data.data) || res.data || {};
                let apkFound = false;
                let adImageFound = false;
                (data.policies || []).forEach((policy) => {
                    if (policy.id === 'app_manage.apk') { apkPolicy.value = policy; apkFound = true; }
                    if (policy.id === 'app_manage.ad_image') { adImagePolicy.value = policy; adImageFound = true; }
                });
                if (!apkFound || !adImageFound) throw new Error('upload policy unavailable');
                uploadPolicyReady.value = true;
            }).catch(() => {
                uploadPolicyReady.value = false;
                MessagePlugin.error('获取 App 上传规则失败，请刷新重试');
            });
            const fileMatchesPolicy = (file, policy) => {
                const extension = String(file.name || '').split('.').pop().toLowerCase();
                return policy.extensions.includes(extension)
                    && file.size <= policy.max_size_mb * 1024 * 1024;
            };

            // TinyMCE vendor 懒加载：编辑器仅弹窗内使用，首次打开弹窗前动态注入脚本，
            // 避免三页签页面每次 SPA 导航都重复 eval 大体积 vendor（Promise 缓存幂等）
            let tinymceLoading = null;
            const ensureTinymce = () => {
                if (window.tinymce) return Promise.resolve();
                if (!tinymceLoading) {
                    tinymceLoading = new Promise((resolve, reject) => {
                        const s = document.createElement('script');
                        s.src = '/static/vendor/tinymce/tinymce.min.js?v=' + ((window.__PAGE_DATA__ && window.__PAGE_DATA__.appVersion) || '');
                        s.onload = resolve;
                        s.onerror = () => { tinymceLoading = null; reject(new Error('TinyMCE 加载失败')); };
                        document.head.appendChild(s);
                    });
                }
                return tinymceLoading;
            };

            // 文件大小人性化显示
            const formatSize = (bytes) => {
                if (!bytes) return '0 B';
                const units = ['B', 'KB', 'MB', 'GB'];
                let value = bytes, i = 0;
                while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
                return value.toFixed(value >= 100 || i === 0 ? 0 : 1) + ' ' + units[i];
            };

            // ------------------------- Tab1 App版本 -------------------------
            const versionData = ref([]);
            const versionLoading = ref(false);
            const versionPagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const versionColumns = [
                { colKey: 'version', title: '版本', minWidth: 140, cell: 'version' },
                { colKey: 'build_number', title: '构建号', width: 100, cell: 'build_number' },
                { colKey: 'apk_size', title: 'APK大小', width: 110, cell: 'apk_size' },
                { colKey: 'apk_md5', title: 'MD5', width: 280 },
                { colKey: 'update_policy', title: '更新策略', width: 110, cell: 'update_policy' },
                { colKey: 'status', title: '状态', width: 90, cell: 'status' },
                { colKey: 'create_time', title: '发布时间', width: 175 },
                { colKey: 'actions', title: '操作', width: 130, cell: 'actions' }
            ];

            const fetchVersions = () => {
                versionLoading.value = true;
                request.get('/app_manage/versions', {
                    params: { page: versionPagination.current, limit: versionPagination.pageSize }
                }).then((res) => {
                    const data = res.data.data || {};
                    versionData.value = data.list || [];
                    versionPagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { versionLoading.value = false; });
            };

            const onVersionPageChange = (pageInfo) => {
                versionPagination.current = pageInfo.current;
                versionPagination.pageSize = pageInfo.pageSize;
                fetchVersions();
            };

            // 上传发版
            const versionUploadVisible = ref(false);
            const versionSubmitting = ref(false);
            const apkFiles = ref([]);
            const clUploadEditor = ref(null);
            const versionForm = reactive({
                version_name: '', version_code: null, build_number: '', changelog: '', update_policy: 1, status: 1, apk: ''
            });
            const versionUploadFormRef = ref(null);
            const versionUploadRules = {
                apk: [{ validator: () => ({ result: apkFiles.value.length > 0, message: '请选择APK文件' }), type: 'error' }],
                version_name: [{ required: true, message: '请输入版本名', type: 'error' }],
                version_code: [{ required: true, message: '请输入版本号', type: 'error' }]
            };

            const openVersionUpload = () => {
                if (!uploadPolicyReady.value) { MessagePlugin.error('上传规则尚未加载完成'); return; }
                versionForm.version_name = '';
                versionForm.version_code = null;
                versionForm.build_number = '';
                versionForm.changelog = '';
                versionForm.update_policy = 1;
                versionForm.status = 1;
                apkFiles.value = [];
                // 弹窗内含富文本编辑器，先确保 vendor 就绪再打开（com-tinymce mounted 即初始化）
                ensureTinymce().then(() => { versionUploadVisible.value = true; })
                    .catch(() => { MessagePlugin.error('编辑器资源加载失败，请刷新重试'); });
            };

            const submitVersionUpload = () => {
                if (!versionUploadFormRef.value) return;
                versionUploadFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    if (!uploadPolicyReady.value) { MessagePlugin.error('上传规则尚未加载完成'); return; }
                    if (!fileMatchesPolicy(apkFiles.value[0].raw, apkPolicy.value)) {
                        MessagePlugin.error('APK 格式或大小不符合上传设置');
                        return;
                    }
                    versionSubmitting.value = true;
                    // multipart 表单提交：t-upload 收集的原生 File 放入 FormData，
                    // 不手工设 Content-Type（request.js 拦截器已对 FormData 剥离实例默认
                    // JSON 头，由浏览器自动携带 multipart boundary）
                    const fd = new FormData();
                    fd.append('apk', apkFiles.value[0].raw);
                    fd.append('version_name', versionForm.version_name.trim());
                    fd.append('version_code', versionForm.version_code);
                    fd.append('build_number', versionForm.build_number.trim());
                    fd.append('changelog', clUploadEditor.value ? clUploadEditor.value.getContent() : versionForm.changelog);
                    fd.append('update_policy', versionForm.update_policy);
                    fd.append('status', versionForm.status);
                    request.post('/app_manage/versions', fd).then(() => {
                        MessagePlugin.success('发布成功');
                        versionUploadVisible.value = false;
                        fetchVersions();
                    }).catch(() => {}).finally(() => { versionSubmitting.value = false; });
                });
            };

            // 编辑版本
            const versionEditVisible = ref(false);
            const versionEditId = ref(0);
            const clEditEditor = ref(null);
            const versionEditForm = reactive({ build_number: '', changelog: '', update_policy: 1, status: 1 });

            const openVersionEdit = (row) => {
                versionEditId.value = row.id;
                versionEditForm.build_number = row.build_number || '';
                versionEditForm.changelog = row.changelog || '';
                versionEditForm.update_policy = row.update_policy;
                versionEditForm.status = row.status;
                ensureTinymce().then(() => { versionEditVisible.value = true; })
                    .catch(() => { MessagePlugin.error('编辑器资源加载失败，请刷新重试'); });
            };

            const submitVersionEdit = () => {
                request.put('/app_manage/versions/' + versionEditId.value, {
                    build_number: versionEditForm.build_number.trim(),
                    changelog: clEditEditor.value ? clEditEditor.value.getContent() : versionEditForm.changelog,
                    update_policy: versionEditForm.update_policy,
                    status: versionEditForm.status
                }).then(() => {
                    MessagePlugin.success('更新成功');
                    versionEditVisible.value = false;
                    fetchVersions();
                }).catch(() => {});
            };

            const removeVersion = (row) => {
                request.delete('/app_manage/versions/' + row.id).then(() => {
                    MessagePlugin.success('删除成功');
                    fetchVersions();
                }).catch(() => {});
            };

            // ------------------------- Tab2 开屏广告 -------------------------
            const ad = ref({});
            const adForm = reactive({
                link_url: '', duration: 3, enabled: 0
            });
            // 投放时段 range-picker 绑定数组：[开始, 结束]，全空=不限时段
            const adRange = ref([]);
            const adSaving = ref(false);

            const fetchAd = () => {
                request.get('/app_manage/ad').then((res) => {
                    const data = (res.data.data && res.data.data.ad) || {};
                    ad.value = data;
                    adForm.link_url = data.link_url || '';
                    adRange.value = (data.start_time && data.end_time) ? [data.start_time, data.end_time] : [];
                    adForm.duration = data.duration || 3;
                    adForm.enabled = data.enabled || 0;
                }).catch(() => {
                    // 错误可见化：避免权限不足/网络异常时静默失败，造成「上传成功但不显示」假象
                    MessagePlugin.error('广告配置获取失败，请检查权限或网络');
                });
            };

            // image-upload 组件通过此策略复用 App 专用上传策略与服务端字段校验。
            const uploadAdImage = (file, fd) => {
                if (!uploadPolicyReady.value || !fileMatchesPolicy(file, adImagePolicy.value)) {
                    MessagePlugin.error('广告图格式或大小不符合上传设置');
                    return Promise.reject(new Error('invalid ad image policy'));
                }
                return request.post('/app_manage/ad/image', fd).then((res) => {
                    const data = res.data.data || {};
                    if (data.cache_key) { ad.value.cache_key = data.cache_key; }
                    MessagePlugin.success('上传成功，App端将拉取新素材');
                    fetchAd();
                    return res;
                });
            };

            const submitAd = () => {
                if (adForm.enabled === 1 && !ad.value.image_url) {
                    MessagePlugin.warning('请先上传广告图再启用'); return;
                }
                adSaving.value = true;
                const range = adRange.value || [];
                request.put('/app_manage/ad', {
                    link_url: adForm.link_url,
                    start_time: range[0] || null,
                    end_time: range[1] || null,
                    duration: adForm.duration,
                    enabled: adForm.enabled
                }).then(() => {
                    MessagePlugin.success('保存成功');
                    fetchAd();
                }).catch(() => {}).finally(() => { adSaving.value = false; });
            };

            // ------------------------- Tab3 App公告 -------------------------
            const noticeData = ref([]);
            const noticeLoading = ref(false);
            const noticePagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const noticeColumns = [
                { colKey: 'title', title: '标题', minWidth: 180 },
                { colKey: 'is_popup', title: '提示方式', width: 100, cell: 'is_popup' },
                { colKey: 'window', title: '生效时间窗', width: 300, cell: 'window' },
                { colKey: 'enabled', title: '状态', width: 90, cell: 'enabled' },
                { colKey: 'sort_order', title: '排序', width: 80 },
                { colKey: 'create_time', title: '创建时间', width: 175 },
                { colKey: 'actions', title: '操作', width: 130, cell: 'actions' }
            ];

            const fetchNotices = () => {
                noticeLoading.value = true;
                request.get('/app_manage/notices', {
                    params: { page: noticePagination.current, limit: noticePagination.pageSize }
                }).then((res) => {
                    const data = res.data.data || {};
                    noticeData.value = data.list || [];
                    noticePagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { noticeLoading.value = false; });
            };

            const onNoticePageChange = (pageInfo) => {
                noticePagination.current = pageInfo.current;
                noticePagination.pageSize = pageInfo.pageSize;
                fetchNotices();
            };

            const noticeFormVisible = ref(false);
            const noticeFormTitle = ref('新建公告');
            const noticeEditingId = ref(0);
            const noticeEditor = ref(null);
            const noticeForm = reactive({
                title: '', content: '', is_popup: 0, enabled: 1, sort_order: 0
            });
            // 生效时间窗 range-picker 绑定数组：[开始, 结束]，全空=立即生效且永久有效
            const noticeRange = ref([]);
            const noticeFormRef = ref(null);
            const noticeRules = {
                title: [{ required: true, message: '请输入公告标题', type: 'error' }]
            };

            const openNoticeCreate = () => {
                noticeEditingId.value = 0;
                noticeFormTitle.value = '新建公告';
                noticeForm.title = '';
                noticeForm.content = '';
                noticeForm.is_popup = 0;
                noticeRange.value = [];
                noticeForm.enabled = 1;
                noticeForm.sort_order = 0;
                ensureTinymce().then(() => { noticeFormVisible.value = true; })
                    .catch(() => { MessagePlugin.error('编辑器资源加载失败，请刷新重试'); });
            };

            const openNoticeEdit = (row) => {
                noticeEditingId.value = row.id;
                noticeFormTitle.value = '编辑公告';
                noticeForm.title = row.title;
                noticeForm.content = row.content || '';
                noticeForm.is_popup = row.is_popup;
                noticeRange.value = (row.start_time && row.end_time) ? [row.start_time, row.end_time] : [];
                noticeForm.enabled = row.enabled;
                noticeForm.sort_order = row.sort_order || 0;
                ensureTinymce().then(() => { noticeFormVisible.value = true; })
                    .catch(() => { MessagePlugin.error('编辑器资源加载失败，请刷新重试'); });
            };

            const submitNotice = () => {
                if (!noticeFormRef.value) return;
                noticeFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    const range = noticeRange.value || [];
                    const payload = {
                        title: noticeForm.title.trim(),
                        content: noticeEditor.value ? noticeEditor.value.getContent() : noticeForm.content,
                        is_popup: noticeForm.is_popup,
                        start_time: range[0] || null,
                        end_time: range[1] || null,
                        enabled: noticeForm.enabled,
                        sort_order: noticeForm.sort_order
                    };
                    const req = noticeEditingId.value
                        ? request.put('/app_manage/notices/' + noticeEditingId.value, payload)
                        : request.post('/app_manage/notices', payload);
                    req.then(() => {
                        MessagePlugin.success(noticeEditingId.value ? '更新成功' : '创建成功');
                        noticeFormVisible.value = false;
                        fetchNotices();
                    }).catch(() => {});
                });
            };

            const removeNotice = (row) => {
                request.delete('/app_manage/notices/' + row.id).then(() => {
                    MessagePlugin.success('删除成功');
                    fetchNotices();
                }).catch(() => {});
            };

            onMounted(() => {
                fetchVersions();
                fetchAd();
                fetchNotices();
                fetchUploadPolicies();
            });

            return {
                activeTab, formatSize, apkAccept, adImageAccept, apkHint, adImageHint, adImagePolicy, uploadPolicyReady,
                versionData, versionLoading, versionPagination, versionColumns, onVersionPageChange,
                versionUploadVisible, versionSubmitting, apkFiles, versionForm, versionUploadFormRef, versionUploadRules, clUploadEditor,
                openVersionUpload, submitVersionUpload,
                versionEditVisible, versionEditForm, clEditEditor, openVersionEdit, submitVersionEdit, removeVersion,
                ad, adForm, adRange, adSaving, uploadAdImage, submitAd,
                noticeData, noticeLoading, noticePagination, noticeColumns, onNoticePageChange,
                noticeFormVisible, noticeFormTitle, noticeForm, noticeFormRef, noticeRules, noticeRange, noticeEditor,
                openNoticeCreate, openNoticeEdit, submitNotice, removeNotice
            };
        }
    });
})();
