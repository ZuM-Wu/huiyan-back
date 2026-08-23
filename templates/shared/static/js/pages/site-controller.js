/**
 * 官网主题控制器页面脚本
 * 对应路由：/admin/site-controller
 *
 * 后端接口（前缀 /api/admin/v1，见 request.js baseURL）：
 *   GET    /site/config                       读取全部配置
 *   POST   /site/config                       批量写入（Banner / 简介）
 *   POST   /site/nav                          新增导航项
 *   PUT    /site/nav/{index}                  编辑导航项
 *   DELETE /site/nav/{index}                  删除导航项
 *   POST   /site/footer                       新增页脚分组
 *   PUT    /site/footer/{index}               编辑分组标题
 *   DELETE /site/footer/{index}               删除分组
 *   POST   /site/footer/{index}/link          新增分组内链接
 *   PUT    /site/footer/{index}/link/{li}     编辑链接
 *   DELETE /site/footer/{index}/link/{li}     删除链接
 *
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 */
(function () {
    var reactive = Vue.reactive;
    var ref = Vue.ref;
    var onMounted = Vue.onMounted;
    var watch = Vue.watch;
    var MessagePlugin = TDesign.MessagePlugin;

    HuiYan.createPage({
        setup: function () {
            // ========== 全局状态 ==========
            // 主 Tab 状态：初始值从 URL ?tab= 读取，切换时写回 URL（刷新不丢 Tab）
            var activeTab = ref(HuiYan.getUrlTab('nav', ['nav', 'footer', 'banner', 'intro']));
            watch(activeTab, function (val) { HuiYan.syncUrlTab(val); });
            var saving = ref(false);

            // ========== 导航配置 ==========
            var navList = reactive([]);
            var navColumns = [
                { colKey: 'index', title: '序号', width: 60, cell: function (h, ctx) { return ctx.rowIndex + 1; } },
                { colKey: 'label', title: '导航名称' },
                { colKey: 'href', title: '链接地址', ellipsis: true },
                { colKey: 'target', title: '打开方式', width: 100,
                    cell: function (h, ctx) { return ctx.row.target === '_blank' ? '新窗口' : '当前窗口'; } },
                { colKey: 'visible', title: '是否展示', width: 90 },
                { colKey: 'action', title: '操作', width: 120 }
            ];

            // 导航弹窗状态
            var navDialogVisible = ref(false);
            var navEditIndex = ref(-1);
            var navSaving = ref(false);
            var navForm = reactive({ label: '', href: '', target: '_self', visible: 1 });
            // 表单内联校验（视觉规范 9.9）：必填提示显示在输入框下方，禁止 MessagePlugin 弹出
            var navFormRef = ref(null);
            var navRules = {
                label: [{ required: true, message: '请输入导航名称', type: 'error' }],
                href: [{ required: true, message: '请输入链接地址', type: 'error' }]
            };

            // ========== 页脚配置 ==========
            var footerList = reactive([]);
            var footerLinkColumns = [
                { colKey: 'index', title: '序号', width: 50, cell: function (h, ctx) { return ctx.rowIndex + 1; } },
                { colKey: 'label', title: '链接名称' },
                { colKey: 'href', title: '链接地址', ellipsis: true },
                { colKey: 'linkAction', title: '操作', width: 100 }
            ];

            // 分组弹窗状态
            var footerDialogVisible = ref(false);
            var footerEditIndex = ref(-1);
            var footerSaving = ref(false);
            var footerForm = reactive({ title: '' });
            var footerFormRef = ref(null);
            var footerRules = {
                title: [{ required: true, message: '请输入分组标题', type: 'error' }]
            };

            // 链接弹窗状态
            var linkDialogVisible = ref(false);
            var linkGroupIndex = ref(-1);
            var linkEditIndex = ref(-1);
            var linkSaving = ref(false);
            var linkForm = reactive({ label: '', href: '' });
            var linkFormRef = ref(null);
            var linkRules = {
                label: [{ required: true, message: '请输入链接名称', type: 'error' }],
                href: [{ required: true, message: '请输入链接地址', type: 'error' }]
            };

            // ========== Banner + 简介 ==========
            var bannerForm = reactive({ title: '', subtitle: '', background: '' });
            var introText = ref('');

            // ========== 数据加载 ==========

            /** 一次性拉取全部官网配置并分发到各响应式变量 */
            var loadAll = function () {
                return request.get('/site/config').then(function (res) {
                    var body = res.data.data || res.data || {};
                    // 导航
                    navList.splice(0, navList.length, ...(body.nav || []));
                    // 页脚
                    footerList.splice(0, footerList.length, ...(body.footer || []));
                    // Banner
                    var b = body.banner || {};
                    bannerForm.title = b.title || '';
                    bannerForm.subtitle = b.subtitle || '';
                    bannerForm.background = b.background || '';
                    // 简介
                    introText.value = body.intro || '';
                }).catch(function () {
                    MessagePlugin.error('配置加载失败');
                });
            };

            // ========== 导航 CRUD ==========

            /** 打开导航编辑/新增弹窗 */
            var openNavDialog = function (index) {
                navEditIndex.value = index;
                if (index >= 0 && navList[index]) {
                    var item = navList[index];
                    navForm.label = item.label || '';
                    navForm.href = item.href || '';
                    navForm.target = item.target || '_self';
                    navForm.visible = item.visible !== undefined ? item.visible : 1;
                } else {
                    navForm.label = '';
                    navForm.href = '';
                    navForm.target = '_self';
                    navForm.visible = 1;
                }
                navDialogVisible.value = true;
                // 清除上一次未通过的校验提示
                if (navFormRef.value) navFormRef.value.clearValidate();
            };

            /** 提交导航新增/编辑 */
            var submitNav = function () {
                if (!navFormRef.value) return;
                // 内联校验：未通过时错误已显示在对应输入框下方，直接中断
                navFormRef.value.validate().then(function (valid) {
                    if (valid !== true) return;
                    navSaving.value = true;
                    var payload = {
                        label: navForm.label.trim(),
                        href: navForm.href.trim(),
                        target: navForm.target,
                        visible: navForm.visible
                    };
                    var promise;
                    if (navEditIndex.value >= 0) {
                        promise = request.put('/site/nav/' + navEditIndex.value, payload);
                    } else {
                        promise = request.post('/site/nav', payload);
                    }
                    promise.then(function () {
                        MessagePlugin.success(navEditIndex.value >= 0 ? '导航已更新' : '导航已添加');
                        navDialogVisible.value = false;
                        return loadAll();
                    }).catch(function (err) {
                        var d = err && err.response && err.response.data && err.response.data.detail;
                        MessagePlugin.error(d || '操作失败');
                    }).finally(function () { navSaving.value = false; });
                });
            };

            /** 删除导航项 */
            var deleteNav = function (index) {
                request.delete('/site/nav/' + index).then(function () {
                    MessagePlugin.success('导航已删除');
                    return loadAll();
                }).catch(function () {
                    MessagePlugin.error('删除失败');
                });
            };

            /** 导航展示开关切换 */
            var onNavToggle = function (index, row) {
                request.put('/site/nav/' + index, {
                    label: row.label,
                    href: row.href,
                    target: row.target,
                    visible: row.visible
                }).catch(function () {
                    MessagePlugin.error('更新失败');
                    loadAll();
                });
            };

            // ========== 页脚分组 CRUD ==========

            var openFooterDialog = function (index) {
                footerEditIndex.value = index;
                footerForm.title = (index >= 0 && footerList[index]) ? footerList[index].title : '';
                footerDialogVisible.value = true;
                if (footerFormRef.value) footerFormRef.value.clearValidate();
            };

            var submitFooterGroup = function () {
                if (!footerFormRef.value) return;
                footerFormRef.value.validate().then(function (valid) {
                    if (valid !== true) return;
                    footerSaving.value = true;
                    var payload = { title: footerForm.title.trim() };
                    var promise;
                    if (footerEditIndex.value >= 0) {
                        promise = request.put('/site/footer/' + footerEditIndex.value, payload);
                    } else {
                        promise = request.post('/site/footer', payload);
                    }
                    promise.then(function () {
                        MessagePlugin.success(footerEditIndex.value >= 0 ? '分组已更新' : '分组已添加');
                        footerDialogVisible.value = false;
                        return loadAll();
                    }).catch(function (err) {
                        var d = err && err.response && err.response.data && err.response.data.detail;
                        MessagePlugin.error(d || '操作失败');
                    }).finally(function () { footerSaving.value = false; });
                });
            };

            var deleteFooterGroup = function (gi) {
                request.delete('/site/footer/' + gi).then(function () {
                    MessagePlugin.success('分组已删除');
                    return loadAll();
                }).catch(function () { MessagePlugin.error('删除失败'); });
            };

            // ========== 页脚链接 CRUD ==========

            var openFooterLinkDialog = function (gi, li) {
                linkGroupIndex.value = gi;
                linkEditIndex.value = li;
                if (li >= 0 && footerList[gi] && footerList[gi].links && footerList[gi].links[li]) {
                    linkForm.label = footerList[gi].links[li].label || '';
                    linkForm.href = footerList[gi].links[li].href || '';
                } else {
                    linkForm.label = '';
                    linkForm.href = '';
                }
                linkDialogVisible.value = true;
                if (linkFormRef.value) linkFormRef.value.clearValidate();
            };

            var submitFooterLink = function () {
                if (!linkFormRef.value) return;
                linkFormRef.value.validate().then(function (valid) {
                    if (valid !== true) return;
                    linkSaving.value = true;
                    var payload = { label: linkForm.label.trim(), href: linkForm.href.trim() };
                    var gi = linkGroupIndex.value;
                    var li = linkEditIndex.value;
                    var promise;
                    if (li >= 0) {
                        promise = request.put('/site/footer/' + gi + '/link/' + li, payload);
                    } else {
                        promise = request.post('/site/footer/' + gi + '/link', payload);
                    }
                    promise.then(function () {
                        MessagePlugin.success(li >= 0 ? '链接已更新' : '链接已添加');
                        linkDialogVisible.value = false;
                        return loadAll();
                    }).catch(function (err) {
                        var d = err && err.response && err.response.data && err.response.data.detail;
                        MessagePlugin.error(d || '操作失败');
                    }).finally(function () { linkSaving.value = false; });
                });
            };

            var deleteFooterLink = function (gi, li) {
                request.delete('/site/footer/' + gi + '/link/' + li).then(function () {
                    MessagePlugin.success('链接已删除');
                    return loadAll();
                }).catch(function () { MessagePlugin.error('删除失败'); });
            };

            // ========== Banner / 简介保存 ==========

            var saveBanner = function () {
                saving.value = true;
                request.post('/site/config', {
                    banner: { title: bannerForm.title, subtitle: bannerForm.subtitle, background: bannerForm.background }
                }).then(function () {
                    MessagePlugin.success('Banner 已保存');
                }).catch(function () {
                    MessagePlugin.error('保存失败');
                }).finally(function () { saving.value = false; });
            };

            var saveIntro = function () {
                saving.value = true;
                request.post('/site/config', { intro: introText.value })
                    .then(function () {
                        MessagePlugin.success('简介已保存');
                    }).catch(function () {
                        MessagePlugin.error('保存失败');
                    }).finally(function () { saving.value = false; });
            };

            // ========== 初始化 ==========

            onMounted(function () { loadAll(); });

            return {
                activeTab: activeTab,
                saving: saving,
                // 导航
                navList: navList,
                navColumns: navColumns,
                navDialogVisible: navDialogVisible,
                navEditIndex: navEditIndex,
                navSaving: navSaving,
                navForm: navForm,
                navFormRef: navFormRef,
                navRules: navRules,
                openNavDialog: openNavDialog,
                submitNav: submitNav,
                deleteNav: deleteNav,
                onNavToggle: onNavToggle,
                // 页脚
                footerList: footerList,
                footerLinkColumns: footerLinkColumns,
                footerDialogVisible: footerDialogVisible,
                footerEditIndex: footerEditIndex,
                footerSaving: footerSaving,
                footerForm: footerForm,
                footerFormRef: footerFormRef,
                footerRules: footerRules,
                openFooterDialog: openFooterDialog,
                submitFooterGroup: submitFooterGroup,
                deleteFooterGroup: deleteFooterGroup,
                // 页脚链接
                linkDialogVisible: linkDialogVisible,
                linkEditIndex: linkEditIndex,
                linkSaving: linkSaving,
                linkForm: linkForm,
                linkFormRef: linkFormRef,
                linkRules: linkRules,
                openFooterLinkDialog: openFooterLinkDialog,
                submitFooterLink: submitFooterLink,
                deleteFooterLink: deleteFooterLink,
                // Banner + 简介
                bannerForm: bannerForm,
                introText: introText,
                saveBanner: saveBanner,
                saveIntro: saveIntro
            };
        }
    });
})();
