/** 插件入口在页面壳加载公共运行时后，顺序加载硬件公共依赖。 */
(async function () {
    'use strict';
    const host = document.getElementById('page-app');
    const context = document.getElementById('plugin-page-context');
    const active = () => host.isConnected && document.getElementById('plugin-page-context') === context;
    const dependencies = ['vendor/echarts/echarts.min.js', 'js/pages/hardware-quick-detect.js',
        'js/components/hardware-detail.js', 'js/pages/hardware-device.js',
        'js/components/hardware-management-template.js', 'js/components/hardware-management.js'];
    try {
        for (const path of dependencies) {
            if (!active()) return;
            await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = '/static/' + path + '?v=3.4.11-hardware-delete-1&library=1';
                script.onload = () => { script.remove(); resolve(); };
                script.onerror = () => { script.remove(); reject(new Error('页面资源加载失败')); };
                document.body.appendChild(script);
            });
        }
        if (!active()) return;
        host.innerHTML = window.HuiYanHardwareManagementTemplate;
        HuiYan.createPluginPage({ plugin: 'hardware_huiyan', page: 'index', setup: window.HuiYanHardwareManagement.setup });
    } catch (_) {
        if (active()) {
            host.removeAttribute('v-cloak');
            host.textContent = '管理页面资源加载失败，请刷新页面重试';
        }
    }
})();
