(function () {
    'use strict';

    var MAX_STRUCTURAL_EVENTS_PER_TICK = 32;
    var HIDDEN_TICK_MS = 16;
    var VISIBLE_DELTA_EVENTS = {
        TEXT_BLOCK_DELTA: true,
        THINKING_BLOCK_DELTA: true,
        TOOL_CALL_DELTA: true,
        TOOL_RESULT_TEXT_DELTA: true,
        TOOL_RESULT_DATA_DELTA: true,
        DATA_BLOCK_DELTA: true
    };
    var TERMINAL_EVENTS = {
        TEXT_BLOCK_END: true,
        THINKING_BLOCK_END: true,
        TOOL_CALL_END: true,
        TOOL_RESULT_END: true,
        DATA_BLOCK_END: true,
        REQUIRE_USER_CONFIRM: true,
        REPLY_END: true
    };

    function create(options) {
        var queue = [], buffer = '', reader = null, scheduled = null, stopped = false;
        var decoder = new TextDecoder('utf-8'), drainWaiters = [];

        function parseFrame(frame) {
            var value = frame.split('\n').filter(function (line) {
                return line.indexOf('data:') === 0;
            }).map(function (line) {
                return line.slice(5).trimStart();
            }).join('\n');
            if (!value) return;
            try {
                queue.push(JSON.parse(value));
            } catch (err) {
                console.warn('[AI Chat] SSE 事件解析失败', err);
            }
        }

        function append(value, final) {
            buffer += value.replace(/\r\n/g, '\n');
            var frames = buffer.split('\n\n');
            buffer = frames.pop();
            frames.forEach(parseFrame);
            if (final && buffer.trim()) parseFrame(buffer);
            if (final) buffer = '';
            schedule();
        }

        function resolveDrained() {
            if (queue.length || scheduled) return;
            drainWaiters.splice(0).forEach(function (resolve) { resolve(); });
        }

        function drain() {
            scheduled = null;
            if (stopped) { queue = []; resolveDrained(); return; }
            var handled = 0, committedVisibleChange = false;
            while (queue.length && handled < MAX_STRUCTURAL_EVENTS_PER_TICK) {
                var next = queue[0];
                // 结束事件必须留到下一次调度，给前一段增量一次真实绘制机会。
                if (handled && TERMINAL_EVENTS[next.type]) break;
                queue.shift();
                options.onEvent(next);
                handled += 1;
                if (VISIBLE_DELTA_EVENTS[next.type] || TERMINAL_EVENTS[next.type]) {
                    committedVisibleChange = true;
                }
                if (committedVisibleChange) break;
            }
            if (queue.length) schedule();
            else resolveDrained();
        }

        function schedule() {
            if (scheduled || stopped || !queue.length) return;
            if (document.hidden) {
                // 后台标签页没有稳定的动画帧，使用近似帧间隔继续按相同预算排空。
                scheduled = { type: 'timer', id: window.setTimeout(drain, HIDDEN_TICK_MS) };
            } else {
                scheduled = { type: 'frame', id: window.requestAnimationFrame(drain) };
            }
        }

        function waitForDrain() {
            if (!queue.length && !scheduled) return Promise.resolve();
            return new Promise(function (resolve) { drainWaiters.push(resolve); });
        }

        async function read(response, signal) {
            reader = response.body.getReader();
            while (!stopped) {
                var chunk = await reader.read();
                if (chunk.done) break;
                append(decoder.decode(chunk.value, { stream: true }), false);
            }
            append(decoder.decode(), true);
            await waitForDrain();
            if (!signal.aborted && !stopped) throw new Error('实时连接已断开');
        }

        function cancel() {
            stopped = true;
            queue = [];
            if (scheduled) {
                if (scheduled.type === 'frame') window.cancelAnimationFrame(scheduled.id);
                else window.clearTimeout(scheduled.id);
                scheduled = null;
            }
            if (reader) reader.cancel().catch(function () {});
            resolveDrained();
        }

        return { read: read, cancel: cancel };
    }

    window.HuiYanAiChatEventStream = { create: create };
})();
