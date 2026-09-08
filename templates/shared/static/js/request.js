/**
 * API 请求封装
 * Axios 实例 + JWT 拦截 + 401 自动跳登录
 * 所有页面共享此文件，通过全局变量 `request` 调用
 */

// 创建 Axios 实例
const request = axios.create({
    baseURL: '/api/admin/v1',
    timeout: 120000,
    headers: { 'Content-Type': 'application/json' }
});

// AgentScope 公共客户端：与后台管理 API 共用 JWT 和 FormData 处理，但使用独立
// 的 /api/ai 根路径，页面不得自行创建 Axios 实例或拼接基础地址。
const agentScopeRequest = axios.create({
    baseURL: '/api/ai',
    timeout: 120000,
    headers: { 'Content-Type': 'application/json' }
});

agentScopeRequest.interceptors.request.use(function (config) {
    var token = localStorage.getItem('admin_token');
    if (token) config.headers.Authorization = 'Bearer ' + token;
    if (config.data instanceof FormData && config.headers) {
        if (typeof config.headers.setContentType === 'function') config.headers.setContentType(null);
        else delete config.headers['Content-Type'];
    }
    return config;
});
agentScopeRequest.interceptors.response.use(function (response) { return response; }, function (error) {
    if (error.response && error.response.status === 401) {
        localStorage.removeItem('admin_token');
        localStorage.removeItem('admin_user');
        window.location.href = '/admin/login';
    }
    return Promise.reject(error);
});
request.agentScope = agentScopeRequest;

// 统一响应信封解包；受控列表页不再同时兼容裸响应和信封响应。
request.unwrapData = function (response) {
    return response && response.data ? response.data.data : null;
};

// 请求拦截器 — 自动附加 JWT token
request.interceptors.request.use(function (config) {
    var token = localStorage.getItem('admin_token');
    if (token) {
        config.headers.Authorization = 'Bearer ' + token;
    }
    // FormData 上传必须剥离实例默认的 JSON Content-Type：
    // axios 1.x 的 transformRequest 遇到 application/json 头会把 FormData
    // 序列化成 JSON（File 对象丢失），剥离后由浏览器自动携带 multipart boundary
    if (config.data instanceof FormData) {
        if (config.headers && typeof config.headers.setContentType === 'function') {
            config.headers.setContentType(null);
        } else if (config.headers) {
            delete config.headers['Content-Type'];
        }
    }
    return config;
}, function (error) {
    return Promise.reject(error);
});

// 响应拦截器 — 处理 401/403 等
request.interceptors.response.use(function (response) {
    // 如果请求配置标记了 skipAutoError，跳过自动错误提示
    if (response.config && response.config.skipAutoError) {
        return response;
    }
    var data = response.data;
    // 响应格式: {status, msg, data}，status 200 表示成功
    if (data.status && data.status !== 200 && data.status !== 0) {
        if (window.TDesign && TDesign.MessagePlugin) {
            TDesign.MessagePlugin.error(data.msg || '操作失败');
        }
    }
    return response;
}, function (error) {
    // 静默探测和页面自行处理错误的请求，不由公共拦截器重复弹出提示。
    if (error.config && error.config.skipAutoError) {
        return Promise.reject(error);
    }
    if (error.response) {
        var status = error.response.status;
        // 后端全局异常处理器统一返回 {status, msg}，优先透传后端提示
        var respData = error.response.data || {};
        var backendMsg = respData.msg || respData.detail || '';
        if (status === 401) {
            localStorage.removeItem('admin_token');
            localStorage.removeItem('admin_user');
            window.location.href = '/admin/login';
        } else if (window.TDesign && TDesign.MessagePlugin) {
            // 403/400/422/500 等：优先展示后端具体原因，缺失时按状态码兜底
            if (backendMsg) {
                TDesign.MessagePlugin.error(backendMsg);
            } else if (status === 403) {
                TDesign.MessagePlugin.error('无权限访问');
            } else {
                TDesign.MessagePlugin.error('服务器错误，请稍后重试');
            }
        }
    }
    return Promise.reject(error);
});
