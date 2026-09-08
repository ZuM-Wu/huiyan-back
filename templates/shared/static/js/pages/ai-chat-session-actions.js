(function () {
    'use strict';

    /*
     * 删除会话需要同时收敛列表、当前消息和 SSE 连接。该页面专属工厂把
     * 状态机与主聊天脚本分开，调用方仍负责提供现有 AgentScope 请求门面。
     */
    function createDeleteHandler(options) {
        return async function (row) {
            var target = options.sessionId(row);
            if (!target || options.deletingSession.value) return;
            if (target === options.selectedSession.value && options.running.value) {
                options.message.warning('请先停止当前回复，再删除对话');
                return;
            }
            var index = options.sessions.value.findIndex(function (item) {
                return options.sessionId(item) === target;
            });
            var wasSelected = target === options.selectedSession.value;
            options.deletingSession.value = target;
            if (wasSelected) options.closeStream();
            try {
                var deleted = false, removeError = null;
                try { await options.remove(target); deleted = true; }
                catch (err) {
                    removeError = err;
                    deleted = Boolean(err && err.response && err.response.status === 404);
                    if (!deleted && options.exists) {
                        try { deleted = !(await options.exists(target)); }
                        catch (verifyError) { deleted = false; }
                    }
                }
                if (!deleted) {
                    if (wasSelected && options.selectedSession.value === target) options.restoreStream(target);
                    options.fail(removeError, '对话删除失败');
                    return;
                }
                options.sessions.value = options.sessions.value.filter(function (item) {
                    return options.sessionId(item) !== target;
                });
                var next = null;
                if (wasSelected) {
                    options.selectedSession.value = '';
                    options.chatItems.value = [];
                    options.running.value = false;
                    options.replyError.value = '';
                    next = options.sessions.value[Math.min(index, options.sessions.value.length - 1)];
                }
                options.message.success('对话已删除');
                if (next) {
                    try { await options.selectSession(options.sessionId(next)); }
                    catch (nextError) { options.fail(nextError, '相邻对话加载失败，请刷新重试'); }
                }
            } finally {
                options.deletingSession.value = '';
            }
        };
    }

    window.HuiYanAiChatSessionActions = { createDeleteHandler: createDeleteHandler };
})();
