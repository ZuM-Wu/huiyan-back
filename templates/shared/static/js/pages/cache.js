/**
 * 缓存管理页面脚本 — 对应路由 /admin/cache
 * 后端仅提供分类清除接口（/cache/clear_all|clear_plugin|clear_config|clear_permission）
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 */
(function () {
    const { ref } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            const clearing = ref('');

            // 可清除的缓存分类（与后端接口一一对应）
            const cacheActions = [
                { key: 'all', title: '全部缓存', desc: '清除系统所有缓存数据', endpoint: '/cache/clear_all', theme: 'danger' },
                { key: 'plugin', title: '插件缓存', desc: '清除插件列表与钩子缓存', endpoint: '/cache/clear_plugin', theme: 'primary' },
                { key: 'config', title: '配置缓存', desc: '清除系统配置项缓存', endpoint: '/cache/clear_config', theme: 'primary' },
                { key: 'permission', title: '权限/菜单缓存', desc: '清除权限节点与菜单树缓存', endpoint: '/cache/clear_permission', theme: 'primary' }
            ];

            // 执行清除（带二次确认）
            const doClear = (item) => {
                const instance = DialogPlugin.confirm({
                    header: '确认清除',
                    body: '确定要清除【' + item.title + '】吗？',
                    onConfirm: () => {
                        clearing.value = item.key;
                        request.post(item.endpoint).then(() => {
                            MessagePlugin.success(item.title + '已清除');
                            instance.destroy();
                        }).catch(() => {
                            MessagePlugin.error('清除失败');
                        }).finally(() => { clearing.value = ''; });
                    }
                });
            };

            return { clearing, cacheActions, doClear };
        }
    });
})();
