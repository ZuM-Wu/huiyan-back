/* 智能识别页面的无状态格式化函数，单独文件避免主页面脚本过长。 */
(function () {
    window.HuiYanYoloFormat = {
        formatConfidence(value) {
            if (value === null || value === undefined) return '-';
            return (Number(value) * 100).toFixed(1) + '%';
        },
        formatBbox(value) {
            return Array.isArray(value)
                ? value.map((item) => Number(item).toFixed(1)).join(', ')
                : '-';
        },
        formatSize(bytes) {
            if (!bytes) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB'];
            let value = Number(bytes);
            let index = 0;
            while (value >= 1024 && index < units.length - 1) {
                value /= 1024;
                index += 1;
            }
            return value.toFixed(value >= 100 || index === 0 ? 0 : 1) + ' ' + units[index];
        },
        shortHash(value) {
            return value ? value.slice(0, 12) + '...' : '-';
        }
    };
})();
