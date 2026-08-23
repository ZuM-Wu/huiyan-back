/**
 * AI 对话共享工具模块 — 后台与农户端复用的 SSE 解析、消息段模型、事件处理
 *
 * 设计要点：
 * - 纯函数 + 上下文对象，不依赖闭包变量，后台/农户端均可复用
 * - handleEvent 接收 ctx 上下文（含 Vue ref 和回调），消除两端重复逻辑
 * - scrollToBottomRaf 使用 rAF 节流，多 token 同帧只触发一次 DOM 写入
 *
 * 依赖：无外部依赖（纯 JS 逻辑）
 * 暴露：window.AiChatUtils
 */
(function (window) {
    'use strict';

    /**
     * 历史消息 → 消息段结构
     * assistant 消息展开为 reasoning/tool/markdown 段；
     * tool 消息按 tool_call_id 回填到前一条 assistant 的 tool 段结果
     * @param {Array} list - 后端返回的消息列表
     * @returns {Array} 前端消息段数组
     */
    function mapHistory(list) {
        var items = [];
        var toolSegIndex = {};
        list.forEach(function (m) {
            if (m.role === 'user') {
                var userSegments = [];
                if ((m.attachments || []).length) {
                    userSegments.push({ type: 'images', items: m.attachments });
                }
                userSegments.push({ type: 'markdown', data: m.content });
                items.push({
                    role: 'user', name: '我', datetime: m.create_time,
                    content: userSegments
                });
            } else if (m.role === 'assistant') {
                var segs = [];
                if (m.reasoning) {
                    segs.push({ type: 'reasoning', data: m.reasoning, done: true, collapsed: true });
                }
                (m.tool_calls || []).forEach(function (tc) {
                    var fn = tc['function'] || {};
                    var seg = {
                        type: 'tool', name: fn.name || '',
                        arguments: fn.arguments || '', result: '',
                        denied: false, done: false, expanded: false
                    };
                    if (tc.id) { toolSegIndex[tc.id] = seg; }
                    segs.push(seg);
                });
                if (m.content) { segs.push({ type: 'markdown', data: m.content }); }
                if (segs.length) {
                    items.push({
                        role: 'assistant', name: 'AI 助手',
                        datetime: m.create_time, content: segs
                    });
                }
            } else if (m.role === 'tool') {
                var seg = toolSegIndex[m.tool_call_id];
                if (seg) {
                    seg.result = m.content;
                    seg.done = true;
                    seg.denied = (m.content || '').indexOf('没有权限') === 0;
                }
            }
        });
        return items;
    }

    /**
     * 取当前 assistant 占位项的指定类型段（无则创建，保证增量写入有落点）
     * @param {object} item - 当前 assistant 消息项（chatList 数组最后一个元素）
     * @param {string} type - 段类型：'reasoning' | 'markdown'
     * @returns {object} 段对象引用
     */
    function ensureSegment(item, type) {
        var seg = null;
        for (var i = item.content.length - 1; i >= 0; i--) {
            if (item.content[i].type === type && !item.content[i].sealed) {
                seg = item.content[i];
                break;
            }
        }
        if (!seg) {
            seg = type === 'reasoning'
                ? { type: 'reasoning', data: '', done: false, collapsed: false }
                : { type: 'markdown', data: '' };
            item.content.push(seg);
        }
        return seg;
    }

    /**
     * 统一 SSE 事件信封处理
     * @param {object} ev - 事件对象 { type, data }
     * @param {object} ctx - 上下文，含以下字段：
     *   - chatList: Vue ref（消息列表）
     *   - loading: Vue ref（骨架屏加载状态）
     *   - conversationId: Vue ref（当前会话 ID）
     *   - onMeta: function(convId) — meta 事件回调（刷新会话列表等）
     *   - onError: function(msg) — error 事件回调（显示错误提示）
     *   - onScroll: function() — 事件处理后回调（滚动到底部）
     */
    function handleEvent(ev, ctx) {
        var item = ctx.chatList.value[ctx.chatList.value.length - 1];
        if (!item || !item.content) { return; }
        var data = ev.data || {};

        switch (ev.type) {
            case 'meta':
                if (!ctx.conversationId.value && data.conversation_id) {
                    ctx.conversationId.value = data.conversation_id;
                    if (ctx.onMeta) { ctx.onMeta(data.conversation_id); }
                }
                break;
            case 'reasoning_delta':
                ctx.loading.value = false;
                ensureSegment(item, 'reasoning').data += data.text || '';
                break;
            case 'content_delta':
                ctx.loading.value = false;
                // 正文开始输出即折叠思维链
                item.content.forEach(function (s) {
                    if (s.type === 'reasoning' && !s.done) {
                        s.done = true;
                        s.collapsed = true;
                    }
                });
                ensureSegment(item, 'markdown').data += data.text || '';
                break;
            case 'tool_call':
                ctx.loading.value = false;
                // 新回合工具调用：封存旧正文段，防止下一回合正文接错落点
                item.content.forEach(function (s) {
                    if (s.type === 'markdown') { s.sealed = true; }
                });
                item.content.push({
                    type: 'tool', name: data.name || '',
                    arguments: data.arguments || '', result: '',
                    denied: false, done: false, expanded: false
                });
                break;
            case 'tool_result':
                for (var i = item.content.length - 1; i >= 0; i--) {
                    var s = item.content[i];
                    if (s.type === 'tool' && s.name === data.name && !s.done) {
                        s.result = data.result || '';
                        s.denied = !!data.denied;
                        s.done = true;
                        break;
                    }
                }
                break;
            case 'done':
                item.content.forEach(function (s) {
                    if (s.type === 'reasoning' && !s.done) {
                        s.done = true;
                        s.collapsed = true;
                    }
                });
                break;
            case 'error':
                ctx.loading.value = false;
                if (ctx.onError) { ctx.onError(data.message || '对话出错'); }
                ensureSegment(item, 'markdown').data +=
                    '\n\n> 出错：' + (data.message || '未知错误');
                break;
        }
        if (ctx.onScroll) { ctx.onScroll(); }
    }

    /**
     * 读取 SSE 流：按 "\n\n" 分帧解析 "data: {JSON}" 信封
     * @param {Response} resp - fetch 返回的 Response 对象
     * @param {function} onEvent - 事件处理回调，接收解析后的事件对象
     * @returns {Promise} 流读取完毕后 resolve
     */
    async function readSseStream(resp, onEvent) {
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';
        for (;;) {
            var chunk = await reader.read();
            if (chunk.done) { break; }
            buffer += decoder.decode(chunk.value, { stream: true });
            var idx;
            while ((idx = buffer.indexOf('\n\n')) >= 0) {
                var frame = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                frame.split('\n').forEach(function (line) {
                    if (line.indexOf('data:') !== 0) { return; }
                    try {
                        onEvent(JSON.parse(line.slice(5).trim()));
                    } catch (e) { /* 忽略残帧 */ }
                });
            }
        }
    }

    /**
     * SSE 流式请求统一入口：注入认证头 + 401 处理 + readSseStream 复用
     * 替代页面内裸 fetch + 手写 Authorization 头，后台/农户端共用
     * @param {string} url - 请求地址（如 /api/admin/v1/ai/chat）
     * @param {object} options - 请求选项：
     *   - body: object - 请求体（自动 JSON 序列化）
     *   - tokenKey: string - localStorage 令牌键名（默认 'admin_token'）
     *   - signal: AbortSignal - 中止信号（可选，onStop 用）
     *   - onEvent: function(ev) - 事件回调，复用 readSseStream 解析
     * @returns {Promise} 流读取完毕后 resolve
     */
    async function fetchSse(url, options) {
        options = options || {};
        var tokenKey = options.tokenKey || 'admin_token';
        var resp = await fetch(url, {
            method: options.method || 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + (localStorage.getItem(tokenKey) || '')
            },
            body: options.body ? JSON.stringify(options.body) : undefined,
            signal: options.signal
        });
        if (resp.status === 401) {
            // 与 request.js 拦截器一致的 401 处理：清令牌跳登录
            localStorage.removeItem(tokenKey);
            if (tokenKey === 'admin_token') {
                localStorage.removeItem('admin_user');
                window.location.href = '/admin/login';
            } else if (location.pathname !== '/farmer/login') {
                location.href = '/farmer/login';
            }
        }
        if (!resp.ok) {
            var detail = 'HTTP ' + resp.status;
            try { detail = (await resp.json()).detail || detail; } catch (e) { /* 忽略 */ }
            throw new Error(detail);
        }
        if (options.onEvent) { await readSseStream(resp, options.onEvent); }
        return resp;
    }

    /**
     * rAF 节流的滚动到底部
     * 多个 token 在同一帧内只触发一次 DOM 写入，降低流式输出时的布局重排开销
     * @param {HTMLElement} el - 滚动容器元素
     */
    var _scrollRafPending = false;
    function scrollToBottomRaf(el) {
        if (!el) { return; }
        if (_scrollRafPending) { return; }
        _scrollRafPending = true;
        requestAnimationFrame(function () {
            _scrollRafPending = false;
            el.scrollTop = el.scrollHeight;
        });
    }

    /**
     * 将 TDesign UploadFile 列表转换为 Data URL。
     * 图片只在当前请求中使用，调用方发送完成后应清空原始文件引用。
     * @param {Array} files - TDesign 上传文件对象或原生 File
     * @returns {Promise<Array<string>>} 图片 Data URL 列表
     */
    function readFilesAsDataUrls(files) {
        var list = Array.isArray(files) ? files : [];
        return Promise.all(list.map(function (item) {
            var file = item && (item.file || item.raw) ? (item.file || item.raw) : item;
            if (!file || typeof file.arrayBuffer !== 'function') {
                return Promise.reject(new Error('图片文件读取失败，请重新选择'));
            }
            return new Promise(function (resolve, reject) {
                var reader = new FileReader();
                reader.onload = function () { resolve(String(reader.result || '')); };
                reader.onerror = function () { reject(new Error('图片文件读取失败，请重试')); };
                reader.readAsDataURL(file);
            });
        }));
    }

    /**
     * 上传聊天图片并返回可直接落库的永久附件元数据。
     */
    async function uploadImages(files, endpoint, tokenKey) {
        var list = Array.isArray(files) ? files : [];
        return Promise.all(list.map(async function (item) {
            var file = item && (item.file || item.raw) ? (item.file || item.raw) : item;
            if (!file) { throw new Error('图片文件读取失败，请重新选择'); }
            item.uploading = true;
            var formData = new FormData();
            formData.append('file', file);
            try {
                var response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + (localStorage.getItem(tokenKey) || '') },
                    body: formData
                });
                var payload = {};
                try { payload = await response.json(); } catch (e) { /* 使用统一错误文案 */ }
                if (!response.ok || payload.status !== 200 || !payload.data) {
                    throw new Error(payload.detail || payload.msg || ('图片上传失败（HTTP ' + response.status + '）'));
                }
                return {
                    type: 'image', url: payload.data.url,
                    name: payload.data.name || file.name,
                    mime_type: payload.data.mime_type || file.type,
                    size: payload.data.size || file.size
                };
            } finally {
                item.uploading = false;
            }
        }));
    }

    // 暴露全局命名空间
    window.AiChatUtils = {
        mapHistory: mapHistory,
        ensureSegment: ensureSegment,
        handleEvent: handleEvent,
        readSseStream: readSseStream,
        fetchSse: fetchSse,
        scrollToBottomRaf: scrollToBottomRaf,
        readFilesAsDataUrls: readFilesAsDataUrls,
        uploadImages: uploadImages
    };
})(window);
