(function () {
    'use strict';

    /*
     * AgentScope 历史消息和 SSE 事件共用同一套可视消息状态。每次流式更新都
     * 替换消息、内容块及 data 引用，确保 TDesign Chat 能立即重绘增量内容。
     */
    function createMessageState(options) {
        var chatItems = options.chatItems;
        var running = options.running;
        var sending = options.sending;
        var replyError = options.replyError;

        function list(value) {
            return Array.isArray(value) ? value : [];
        }

        function dataSource(block) {
            return block && (block.source || block.data && block.data.source) || {};
        }

        function blockText(block) {
            if (!block) return '';
            if (typeof block === 'string') return block;
            if (block.type === 'text' || block.type === 'markdown') return block.text || block.data || '';
            if (block.type === 'thinking') return block.thinking || block.text || block.data && (block.data.text || block.data) || '';
            if (block.type === 'tool_result') return list(block.output).map(blockText).filter(Boolean).join('\n');
            return '';
        }

        function visibleText(value) {
            return typeof value === 'string' && value.trim() ? value : '';
        }

        function historicalToolStatus(block) {
            var state = String(block && block.state || '').toLowerCase();
            if (state === 'asking' || state === 'denied') return 'denied';
            if (state === 'error') return 'error';
            return 'complete';
        }

        function normalizeBlock(block, role, index) {
            var source = dataSource(block);
            var url = source.url || block && block.url;
            if (String(url || '').indexOf('huiyan-upload://common/') === 0) url = '/upload/common/' + String(url).slice(23);
            if (block && (block.type === 'data' || block.type === 'image') && url) {
                return {
                    id: block.id || 'image-' + index,
                    type: 'image',
                    data: { url: url, name: block.name || '图片' }
                };
            }
            if (block && block.type === 'thinking') {
                var thinking = visibleText(blockText(block));
                if (!thinking) return null;
                return {
                    id: block.id || 'thinking-' + index,
                    type: 'thinking',
                    data: { title: '思考过程', text: thinking },
                    status: historicalToolStatus(block),
                    collapsed: true
                };
            }
            if (block && (block.type === 'tool_call' || block.type === 'tool_result')) {
                var callId = block.id || block.tool_call_id || 'tool-' + index;
                return {
                    id: callId,
                    type: 'toolcall-' + callId,
                    data: {
                        toolCallId: callId,
                        toolCallName: block.name || block.tool_call_name || '工具调用',
                        args: block.type === 'tool_call' ? JSON.stringify(block.arguments || block.input || {}) : '',
                        result: block.type === 'tool_result' ? blockText(block) : ''
                    },
                    status: historicalToolStatus(block),
                    collapsed: true
                };
            }
            var text = visibleText(blockText(block));
            if (!text) return null;
            return {
                id: block && block.id || 'text-' + index,
                type: role === 'user' ? 'text' : 'markdown',
                data: text
            };
        }

        function toChatItem(message, index) {
            var role = message && message.role === 'user' ? 'user' : 'assistant';
            var source = Array.isArray(message && message.content)
                ? message.content
                : [{ type: 'text', text: blockText(message && message.content) }];
            var content = [];
            source.forEach(function (block, blockIndex) {
                var normalized = normalizeBlock(block, role, blockIndex);
                if (!normalized) return;
                if (isToolBlock(normalized)) {
                    var existing = content.find(function (item) {
                        return isToolBlock(item) && item.id === normalized.id;
                    });
                    if (existing) {
                        existing.data.args = existing.data.args || normalized.data.args;
                        existing.data.result = existing.data.result || normalized.data.result;
                        if (existing.data.toolCallName === '工具调用') {
                            existing.data.toolCallName = normalized.data.toolCallName;
                        }
                        return;
                    }
                }
                content.push(normalized);
            });
            if (!content.length) return null;
            return {
                id: String(message && (message.id || message.message_id) || index),
                role: role,
                content: content,
                datetime: options.formatTime(message && (message.created_at || message.timestamp)),
                status: '',
                replyStatus: 'complete'
            };
        }

        function normalizeMessages(messages) {
            return list(messages).map(toChatItem).filter(Boolean);
        }

        function pendingConfirmations(messages) {
            return list(messages).map(function (message) {
                var calls = list(message && message.content).filter(function (block) { return block && block.type === 'tool_call' && block.state === 'asking'; });
                if (!calls.length) return null;
                return { type: 'REQUIRE_USER_CONFIRM', reply_id: String(message.id || message.message_id || ''), tool_calls: calls };
            }).filter(function (event) { return event && event.reply_id; });
        }

        function replyItem(replyId) {
            return chatItems.value.find(function (item) { return item.id === String(replyId); });
        }

        function replaceItem(item, content) {
            var next = Object.assign({}, item, { content: content || item.content.slice() });
            chatItems.value = chatItems.value.map(function (row) { return row === item ? next : row; });
            return next;
        }

        function replaceBlock(item, block, changes) {
            var nextBlock = Object.assign({}, block, changes);
            replaceItem(item, item.content.map(function (row) { return row === block ? nextBlock : row; }));
            return nextBlock;
        }

        function revealReply(replyId) {
            var item = replyItem(replyId);
            if (!item || !item.status) return;
            var visible = Object.assign({}, item, { status: '' });
            chatItems.value = chatItems.value.map(function (row) { return row === item ? visible : row; });
        }

        function startReply(event) {
            chatItems.value = chatItems.value.filter(function (item) { return !item.pendingPlaceholder; });
            var item = replyItem(event.reply_id);
            if (!item) {
                item = { id: String(event.reply_id), role: 'assistant', content: [], status: 'streaming' };
                chatItems.value = chatItems.value.concat(item);
            } else {
                item = replaceItem(item, []);
                item.status = 'streaming';
                item = replaceItem(item);
            }
            running.value = true; replyError.value = '';
            return item;
        }

        function eventBlock(event, create) {
            var item = replyItem(event.reply_id);
            if (!item) item = startReply(event);
            var id = event.block_id || event.tool_call_id;
            var block = item.content.find(function (row) { return row.id === id; });
            var created = false;
            if (!block && create) {
                block = create(id);
                created = true;
                item = replaceItem(item, item.content.concat(block));
            }
            return { item: item, block: block, created: created };
        }

        function finishProcessBlocks(item, status) {
            return item.content.map(function (block) {
                if (block.type !== 'thinking' && !isToolBlock(block)) return block;
                return Object.assign({}, block, {
                    status: block.status === 'denied' ? 'denied' : status,
                    collapsed: true
                });
            });
        }

        function handleEvent(event) {
            if (!event || !event.type) return;
            var state;
            var type = event.type;
            if (type === 'REPLY_START') {
                startReply(event);
                options.scrollBottom();
                return;
            }
            if (type === 'TEXT_BLOCK_START') {
                state = eventBlock(event, function (id) {
                    return { id: id, type: 'markdown', data: '', status: 'streaming' };
                });
            } else if (type === 'TEXT_BLOCK_DELTA') {
                var textDelta = event.delta || '';
                if (textDelta) {
                    state = eventBlock(event, function (id) {
                        // 断线恢复或旧事件可能缺失 START，首段必须在创建块时直接写入。
                        return { id: id, type: 'markdown', data: textDelta, status: 'streaming' };
                    });
                    if (state.block && !state.created) {
                        replaceBlock(state.item, state.block, { data: state.block.data + textDelta });
                    }
                }
            } else if (type === 'TEXT_BLOCK_END') {
                state = eventBlock(event);
                if (state.block && state.block.status !== 'complete') {
                    replaceBlock(state.item, state.block, { status: 'complete' });
                }
            } else if (type === 'THINKING_BLOCK_START') {
                state = eventBlock(event, function (id) {
                    return { id: id, type: 'thinking', data: { title: '思考过程', text: '' }, status: 'streaming', collapsed: false };
                });
            } else if (type === 'THINKING_BLOCK_DELTA') {
                var thinkingDelta = event.delta || '';
                if (thinkingDelta) {
                    state = eventBlock(event, function (id) {
                        // 重连补发可能从 DELTA 开始，首段思考也必须立即建立可见内容块。
                        return { id: id, type: 'thinking', data: { title: '思考过程', text: thinkingDelta }, status: 'streaming', collapsed: false };
                    });
                    if (state.block && !state.created) replaceBlock(state.item, state.block, {
                        data: Object.assign({}, state.block.data, { text: state.block.data.text + thinkingDelta }),
                        status: 'streaming', collapsed: false
                    });
                }
            } else if (type === 'THINKING_BLOCK_END') {
                state = eventBlock(event);
                if (state.block && (state.block.status !== 'complete' || !state.block.collapsed)) {
                    replaceBlock(state.item, state.block, { status: 'complete', collapsed: true });
                }
            } else if (type === 'TOOL_CALL_START') {
                state = eventBlock(event, function (id) {
                    return {
                        id: id,
                        type: 'toolcall-' + id,
                        data: {
                            toolCallId: id,
                            toolCallName: event.tool_call_name || '工具调用',
                            args: '',
                            result: ''
                        },
                        status: 'streaming',
                        collapsed: false
                    };
                });
            } else if (type === 'TOOL_CALL_DELTA') {
                var argsDelta = event.delta || '';
                state = argsDelta ? eventBlock(event) : null;
                if (state && state.block) replaceBlock(state.item, state.block, {
                    data: Object.assign({}, state.block.data, { args: state.block.data.args + argsDelta })
                });
            } else if (type === 'TOOL_RESULT_START') {
                state = eventBlock(event, function (id) {
                    return {
                        id: id,
                        type: 'toolcall-' + id,
                        data: {
                            toolCallId: id,
                            toolCallName: event.tool_call_name || '工具调用',
                            args: '',
                            result: ''
                        },
                        status: 'streaming',
                        collapsed: false
                    };
                });
                if (state.block) {
                    replaceBlock(state.item, state.block, { status: 'streaming', collapsed: false });
                }
            } else if (type === 'TOOL_RESULT_TEXT_DELTA' || type === 'TOOL_RESULT_DATA_DELTA') {
                var resultDelta = event.delta || event.url || (type === 'TOOL_RESULT_DATA_DELTA' ? '[二进制结果]' : '');
                state = resultDelta ? eventBlock(event) : null;
                if (state && state.block) replaceBlock(state.item, state.block, {
                    data: Object.assign({}, state.block.data, { result: state.block.data.result + resultDelta })
                });
            } else if (type === 'TOOL_RESULT_END') {
                state = eventBlock(event);
                if (state.block) replaceBlock(state.item, state.block, {
                    status: event.state === 'denied' ? 'denied' : (event.state === 'error' ? 'error' : 'complete'),
                    collapsed: true
                });
            } else if (type === 'REQUIRE_USER_CONFIRM') {
                list(event.tool_calls).forEach(function (call) {
                    var pending = eventBlock({ reply_id: event.reply_id, tool_call_id: call.id }, function (id) {
                        return {
                            id: id,
                            type: 'toolcall-' + id,
                            data: {
                                toolCallId: id,
                                toolCallName: call.name || '工具调用',
                                args: call.input || '',
                                result: ''
                            },
                            status: 'denied',
                            collapsed: true
                        };
                    });
                    if (pending.block) {
                        replaceBlock(pending.item, pending.block, { status: 'denied', collapsed: true });
                    }
                });
            } else if (type === 'DATA_BLOCK_START') {
                eventBlock(event, function (id) {
                    return {
                        id: id,
                        type: 'image',
                        mediaType: event.media_type,
                        rawData: '',
                        data: { name: '生成图片', url: '' }
                    };
                });
            } else if (type === 'DATA_BLOCK_DELTA') {
                state = eventBlock(event);
                if (state.block) replaceBlock(state.item, state.block, {
                    rawData: state.block.rawData + (event.data || '')
                });
            } else if (type === 'DATA_BLOCK_END') {
                state = eventBlock(event);
                if (state.block && state.block.rawData) replaceBlock(state.item, state.block, {
                    data: Object.assign({}, state.block.data, {
                        url: 'data:' + state.block.mediaType + ';base64,' + state.block.rawData
                    })
                });
            } else if (type === 'REPLY_END') {
                state = replyItem(event.reply_id);
                if (state) {
                    var replyStatus = event.finished_reason === 'interrupted'
                        ? 'stop'
                        : (event.finished_reason === 'error' ? 'error' : 'complete');
                    var processStatus = replyStatus === 'error' ? 'error' : 'complete';
                    var content = finishProcessBlocks(state, processStatus);
                    if (replyStatus === 'error') content.push({
                        id: 'error',
                        type: 'text',
                        data: options.replyFailure(event)
                    });
                    var finished = Object.assign({}, state, {
                        status: '',
                        replyStatus: replyStatus,
                        content: content
                    });
                    chatItems.value = chatItems.value.map(function (row) { return row === state ? finished : row; });
                }
                running.value = false; sending.value = false;
            }
            var deltaVisible = type.indexOf('_DELTA') > 0 && Boolean(event.delta || event.url || type === 'TOOL_RESULT_DATA_DELTA');
            var terminalVisible = type === 'THINKING_BLOCK_START' || type === 'TOOL_CALL_START' || type === 'TOOL_RESULT_START' || type === 'THINKING_BLOCK_END' || type === 'TOOL_RESULT_END' || type === 'REQUIRE_USER_CONFIRM' || type === 'DATA_BLOCK_END' || type === 'REPLY_END';
            if (deltaVisible || terminalVisible) revealReply(event.reply_id);
            if (deltaVisible || terminalVisible) options.scrollBottom();
        }

        function markStreamError(message) {
            running.value = false;
            sending.value = false;
            replyError.value = message;
            var pending = chatItems.value.find(function (item) {
                return item.status === 'pending' || item.status === 'streaming';
            });
            if (!pending) return;
            var content = finishProcessBlocks(pending, 'error');
            content.push({ id: 'stream-error', type: 'text', data: message });
            var failed = Object.assign({}, pending, {
                status: '',
                replyStatus: 'error',
                content: content
            });
            chatItems.value = chatItems.value.map(function (row) {
                return row === pending ? failed : row;
            });
        }

        function isToolBlock(block) {
            return Boolean(block && String(block.type || '').indexOf('toolcall-') === 0);
        }

        function toolTitle(block) {
            var labels = { streaming: '调用中', complete: '已完成', error: '调用失败', denied: '已阻止' };
            return block.data.toolCallName + ' · ' + (labels[block.status] || '已完成');
        }

        function toolExpandValue(block) {
            return block.collapsed ? [] : [block.id];
        }

        function eventValue(value) {
            return value && value.detail !== undefined ? value.detail : value;
        }

        function setBlockCollapsed(item, block, value) {
            var latest = replyItem(item.id) || item;
            var target = latest.content.find(function (row) { return row.id === block.id; });
            if (target) replaceBlock(latest, target, { collapsed: Boolean(eventValue(value)) });
        }

        function setToolExpanded(item, block, value) {
            var current = eventValue(value);
            setBlockCollapsed(item, block, !Array.isArray(current) || current.length === 0);
        }

        return {
            normalizeBlock: normalizeBlock,
            normalizeMessages: normalizeMessages,
            pendingConfirmations: pendingConfirmations,
            handleEvent: handleEvent,
            markStreamError: markStreamError,
            isToolBlock: isToolBlock,
            toolTitle: toolTitle,
            toolExpandValue: toolExpandValue,
            setBlockCollapsed: setBlockCollapsed,
            setToolExpanded: setToolExpanded
        };
    }

    window.HuiYanAiChatMessageState = { create: createMessageState };
})();
