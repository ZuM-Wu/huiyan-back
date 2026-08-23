/**
 * 上传限制共享客户端：管理员与农户端图片组件共用同一份服务端策略。
 */
(function (window) {
    'use strict';

    var pending = null;
    function context() {
        var adminToken = localStorage.getItem('admin_token');
        if (adminToken) {
            return { endpoint: '/api/admin/v1/upload/limits', token: adminToken };
        }
        return {
            endpoint: '/api/v1/upload/limits',
            token: localStorage.getItem('farmer_token') || ''
        };
    }

    function getImageLimits() {
        if (pending) return pending;
        var target = context();
        pending = fetch(target.endpoint, {
            headers: target.token ? { Authorization: 'Bearer ' + target.token } : {}
        }).then(function (response) {
            if (!response.ok) throw new Error('limits unavailable');
            return response.json();
        }).then(function (body) {
            var data = body.data || body;
            if (!Array.isArray(data.image_extensions)
                    || !Number.isInteger(Number(data.image_max_size_mb))) {
                throw new Error('invalid upload limits');
            }
            return {
                image_extensions: data.image_extensions.map(function (item) {
                    return String(item).replace(/^\./, '').toLowerCase();
                }),
                image_max_size_mb: Number(data.image_max_size_mb)
            };
        }).finally(function () { pending = null; });
        return pending;
    }

    function toAccept(extensions) {
        var mime = {
            jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
            gif: 'image/gif', webp: 'image/webp'
        };
        return (extensions || []).map(function (ext) { return mime[ext] || '.' + ext; }).join(',');
    }

    window.UploadPolicyClient = {
        getImageLimits: getImageLimits,
        toAccept: toAccept,
        invalidate: function () { pending = null; }
    };
})(window);
