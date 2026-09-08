(function () {
    'use strict';
    var RUNTIME_AGENT_MARKER = 'huiyan.runtime.agent.v3';
    var LEGACY_RUNTIME_AGENT_MARKERS = ['huiyan.runtime.agent.v1', 'huiyan.runtime.agent.v2'];
    var RUNTIME_AGENT_PROMPT = RUNTIME_AGENT_MARKER + '\n你是慧眼护农系统的运行时助手。普通文本和代码必须直接在聊天中完整返回。系统会自动向当前会话提供可用工具；当任务需要且工具已出现在工具列表时，应自主选择并调用，不得要求用户手动绑定 MCP、会话或天气资源。工具列表缺少所需能力时，按实际缺失项说明，不得自行推断用户未绑定产区。';
    var CHAT_MODEL_TYPES = { deepseek_credential: 'deepseek_chat', glm_credential: 'glm_chat', openai_credential: 'openai_chat' };
    var MAX_IMAGES = 4;
    var IMAGE_ONLY_MARKER = String.fromCharCode(8203);
    var STREAM_READY_EVENT = 'huiyan_stream_ready';

    var SenderSuffix = Vue.defineComponent({ props: { renderPresets: { type: Function, required: true }, actions: { type: Array, required: true } },
        setup: function (props) { return function () { return props.renderPresets(props.actions); }; } });

    HuiYan.createPage({
        components: { SenderSuffix: SenderSuffix },
        setup: function () {
            var ref = Vue.ref, computed = Vue.computed, onMounted = Vue.onMounted, onBeforeUnmount = Vue.onBeforeUnmount;
            var MessagePlugin = TDesign.MessagePlugin;
            var loading = ref(false), modelsLoading = ref(false), sending = ref(false), running = ref(false);
            var error = ref(''), replyError = ref(''), uploadPolicyError = ref('');
            var sessions = ref([]), chatItems = ref([]), models = ref([]), attachments = ref([]);
            var selectedAgent = ref(''), selectedSession = ref(''), selectedModelKey = ref(''), draft = ref(''), deletingSession = ref('');
            var reasoningEffort = ref('');
            var messageList = ref(null), uploadLimits = ref(null);
            var streamController = null, streamReady = null, streamSession = '', streamPump = null, recovering = false;
            var rejectedConfirmations = new Set();

            function body(response) { var value = response && response.data; return value && value.status !== undefined && Object.prototype.hasOwnProperty.call(value, 'data') ? value.data : (value === undefined ? response : value); }
            function scope(method, path, data, config) { return request.agentScope[method].call(request.agentScope, path, data, config).then(body); }
            function resource(method, path, data, config) { return request[method].call(request, '/ai-resources' + path, data, config).then(body); }
            function list(value, key) { return Array.isArray(value) ? value : (value && (value[key] || value.items)) || []; }
            function errorText(err, fallback) { var data = err && err.response && err.response.data, detail = data && (data.detail || data.msg);
                if (detail && typeof detail === 'object') detail = detail.message || detail.detail; return detail || (err && err.message) || fallback; }
            function fail(err, fallback) { MessagePlugin.error(errorText(err, fallback)); }
            function record(row) { return (row && row.data) || row || {}; }
            function agentId(row) { return row && (row.id || row.agent_id) || record(row).id || ''; }
            function sessionRecord(row) { return (row && row.session) || row || {}; }
            function sessionId(row) { return sessionRecord(row).id || ''; }
            function sessionConfig(row) { return sessionRecord(row).config || {}; }
            function sessionName(row) { return sessionConfig(row).name || '新对话'; }
            function formatTime(value) {
                if (!value) return '';
                var source = String(value).trim();
                if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(source)) source = source.replace(' ', 'T') + 'Z';
                var date = new Date(source);
                return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
            }
            function sessionTime(row) { var item = sessionRecord(row); return formatTime(item.updated_at || item.created_at || sessionConfig(row).created_at); }

            function modelKey(row) { return row.credential_id + '::' + row.model_name; }
            function modelConfig(row, existing) { var type = row.provider === 'glm_credential' ? 'glm_chat' : (row.protocol === 'openai_responses' ? 'openai_response' : (CHAT_MODEL_TYPES[row.provider] || row.provider)); return { type: type, credential_id: row.credential_id, model: row.model_name, parameters: existing && existing.parameters || {} }; }
            var modelOptions = computed(function () { return models.value.map(function (row) { return { value: modelKey(row), label: row.vendor + ' · ' + (row.label || row.model_name) }; }); });
            function selectedModel() { return models.value.find(function (row) { return modelKey(row) === selectedModelKey.value; }) || null; }
            function sameModel(left, right) { return Boolean(left && right && left.type === right.type && left.credential_id === right.credential_id && left.model === right.model); }

            async function loadModelCatalog() {
                modelsLoading.value = true;
                try {
                    var result = await Promise.all([resource('get', '/connections'), resource('get', '/model-cards')]);
                    var enabled = {};
                    list(result[0], 'list').forEach(function (connection) { if (connection.status === 1 && connection.credential_id) enabled[connection.id] = connection; });
                    models.value = list(result[1], 'list').filter(function (card) { return card.status === 1 && enabled[card.connection_id]; }).map(function (card) {
                        var connection = enabled[card.connection_id];
                        return {
                            credential_id: connection.credential_id, provider: connection.provider, protocol: connection.protocol,
                            vendor: connection.vendor || connection.name || connection.provider,
                            model_name: card.model_name, label: card.label || card.model_name,
                            support_tools: Boolean(card.support_tools), support_vision: Boolean(card.support_vision), support_reasoning: Boolean(card.support_reasoning),
                            reasoning_efforts: Array.isArray(card.reasoning_efforts) ? card.reasoning_efforts : [], default_reasoning_effort: card.default_reasoning_effort || '',
                            default: Boolean(connection.is_default && connection.default_model === card.model_name)
                        };
                    });
                    if (!models.value.some(function (row) { return modelKey(row) === selectedModelKey.value; })) {
                        var preferred = models.value.find(function (row) { return row.default; }) || models.value[0];
                        selectedModelKey.value = preferred ? modelKey(preferred) : '';
                    }
                    resetTurnReasoning();
                } finally { modelsLoading.value = false; }
            }

            async function ensureRuntimeAgent() {
                var rows = list(await scope('get', '/agent/'), 'agents');
                var runtime = rows.find(function (row) {
                    var data = record(row), prompt = String(data.system_prompt || '');
                    return data.name === '__huiyan_runtime_agent__' || prompt.indexOf(RUNTIME_AGENT_MARKER) !== -1 || LEGACY_RUNTIME_AGENT_MARKERS.some(function (marker) { return prompt.indexOf(marker) !== -1; });
                });
                if (!runtime) {
                    var created = await scope('post', '/agent/', {
                        name: '__huiyan_runtime_agent__', system_prompt: RUNTIME_AGENT_PROMPT,
                        context_config: {}, react_config: {}
                    });
                    runtime = { id: created && created.agent_id };
                } else if (record(runtime).system_prompt !== RUNTIME_AGENT_PROMPT) {
                    await scope('patch', '/agent/' + encodeURIComponent(agentId(runtime)), { system_prompt: RUNTIME_AGENT_PROMPT });
                }
                selectedAgent.value = agentId(runtime);
                if (!selectedAgent.value) throw new Error('对话服务初始化失败');
            }

            function selectedSessionModel(row) { var config = sessionConfig(row).chat_model_config;
                return config && models.value.find(function (model) { return model.credential_id === config.credential_id && model.model_name === config.model; }) || null; }
            async function normalizeSessionModel() { var row = sessions.value.find(function (item) { return sessionId(item) === selectedSession.value; });
                var model = selectedSessionModel(row), config = row && sessionConfig(row).chat_model_config;
                if (model && config && !sameModel(config, modelConfig(model, config))) await applyModelToSession(model, true); return model; }
            function syncModelFromSession() { var row = sessions.value.find(function (item) { return sessionId(item) === selectedSession.value; }), model = selectedSessionModel(row);
                if (model) selectedModelKey.value = modelKey(model); }

            async function scrollBottom() {
                await Vue.nextTick();
                if (messageList.value && messageList.value.scrollToBottom) messageList.value.scrollToBottom({ behavior: 'auto' });
            }
            function replyFailure(event) { var value = event && event.error || {}, type = String(value.type || '').toLowerCase(), text = String(value.message || '模型生成失败，请重试');
                var messages = { permission: '模型权限不足，请到 AI 设置检查账号权限和模型权限', rate_limit: '模型调用频率或额度已达到上限，请稍后重试', setup: '会话初始化失败，请到 AI 设置检查模型、工具和知识库', connection: '模型请求失败，请检查模型服务网络或稍后重试', model: '模型请求失败，请检查模型服务网络或稍后重试', provider: '模型请求失败，请检查模型服务网络或稍后重试' };
                if (type === 'authentication' || /authentication failed|api key|credential|鉴权/i.test(text)) text = '模型鉴权失败，请到 AI 设置检查 API Key'; else if (messages[type]) text = messages[type];
                replyError.value = text; return text; }
            var messageState = window.HuiYanAiChatMessageState.create({
                chatItems: chatItems, running: running, sending: sending, replyError: replyError,
                formatTime: formatTime, scrollBottom: scrollBottom, replyFailure: replyFailure
            });
            async function loadMessages(showFailure) {
                if (!selectedAgent.value || !selectedSession.value) { chatItems.value = []; running.value = false; return; }
                try {
                    var result = await scope('get', '/sessions/' + encodeURIComponent(selectedSession.value) + '/messages', { params: { agent_id: selectedAgent.value, limit: 200 } });
                    var messages = list(result, 'messages');
                    chatItems.value = messageState.normalizeMessages(messages);
                    running.value = Boolean(result && result.is_running);
                    await scrollBottom();
                    return messageState.pendingConfirmations(messages);
                } catch (err) { if (showFailure !== false) fail(err, '消息读取失败'); throw err; }
            }

            function closeStream() {
                if (streamPump) streamPump.cancel();
                if (streamController) streamController.abort();
                streamController = null; streamReady = null; streamSession = ''; streamPump = null;
            }
            function confirmationKey(replyId, call) {
                return String(replyId) + ':' + String(call && call.id || '');
            }
            async function rejectConfirmation(event) {
                if (!event || !event.reply_id || !(event.tool_calls || []).length) return;
                var pendingCalls = event.tool_calls.filter(function (call) {
                    return !rejectedConfirmations.has(confirmationKey(event.reply_id, call));
                });
                if (!pendingCalls.length) return;
                pendingCalls.forEach(function (call) { rejectedConfirmations.add(confirmationKey(event.reply_id, call)); });
                running.value = true;
                try {
                    await scope('post', '/chat/', {
                        agent_id: selectedAgent.value,
                        session_id: selectedSession.value,
                        input: {
                            type: 'USER_CONFIRM_RESULT', reply_id: event.reply_id,
                            confirm_results: pendingCalls.map(function (call) { return { confirmed: false, tool_call: call, rules: null }; })
                        }
                    });
                } catch (err) {
                    pendingCalls.forEach(function (call) { rejectedConfirmations.delete(confirmationKey(event.reply_id, call)); });
                    markStreamError('未授权工具已阻止，但回复恢复失败，请刷新后重试');
                }
            }
            function handleStreamEvent(event) {
                messageState.handleEvent(event);
                if (event && event.type === 'REQUIRE_USER_CONFIRM') rejectConfirmation(event);
            }
            async function rejectLegacyConfirmations(events) {
                for (var index = 0; index < events.length; index += 1) await rejectConfirmation(events[index]);
            }
            function markStreamError(message) { messageState.markStreamError(message); }
            async function recoverStream(session) {
                if (recovering || session !== selectedSession.value) return;
                recovering = true;
                try {
                    var pending = await loadMessages(false);
                    await openStream(session, false);
                    await rejectLegacyConfirmations(pending || []);
                } catch (err) { markStreamError('实时连接已中断，请刷新后重试'); }
                finally { recovering = false; }
            }
            function openStream(session, recover) {
                if (!session || !selectedAgent.value) return Promise.resolve(false);
                if (streamController && streamSession === session) return streamReady;
                closeStream();
                var controller = new AbortController(), token = localStorage.getItem('admin_token') || '';
                streamController = controller; streamSession = session;
                var url = '/api/ai/sessions/' + encodeURIComponent(session) + '/stream?agent_id=' + encodeURIComponent(selectedAgent.value);
                streamReady = fetch(url, { headers: { Accept: 'text/event-stream', Authorization: 'Bearer ' + token }, signal: controller.signal }).then(function (response) {
                    if (!response.ok || !response.body) throw new Error('实时连接建立失败');
                    return new Promise(function (resolve, reject) {
                        var ready = false;
                        streamPump = window.HuiYanAiChatEventStream.create({ onEvent: function (event) {
                            if (event && event.type === 'CUSTOM' && event.name === STREAM_READY_EVENT) {
                                ready = true; resolve(true); return;
                            }
                            handleStreamEvent(event);
                        } });
                        streamPump.read(response, controller.signal).catch(function (err) {
                            if (!ready) {
                                if (controller.signal.aborted || session !== selectedSession.value) resolve(false);
                                else reject(err);
                                return;
                            }
                            if (controller.signal.aborted || session !== selectedSession.value) return;
                            if (streamController === controller) { streamController = null; streamReady = null; streamSession = ''; }
                            if (recover !== false) recoverStream(session); else markStreamError(errorText(err, '实时连接已断开'));
                        });
                    });
                }).catch(function (err) {
                    if (streamController === controller) { streamController = null; streamReady = null; streamSession = ''; }
                    throw err;
                });
                return streamReady;
            }

            async function loadSessions() {
                if (!selectedAgent.value) { sessions.value = []; selectedSession.value = ''; return; }
                sessions.value = list(await scope('get', '/sessions/', { params: { agent_id: selectedAgent.value } }), 'sessions');
                if (!sessions.value.some(function (row) { return sessionId(row) === selectedSession.value; })) selectedSession.value = sessions.value[0] ? sessionId(sessions.value[0]) : '';
                syncModelFromSession();
                if (selectedSession.value) await normalizeSessionModel();
                var pending = await loadMessages();
                if (selectedSession.value) await openStream(selectedSession.value, true);
                await rejectLegacyConfirmations(pending || []);
            }
            async function applyModelToSession(model, silent) {
                if (!model || !selectedSession.value) return;
                var currentConfig = sessionConfig(sessions.value.find(function (item) { return sessionId(item) === selectedSession.value; })).chat_model_config;
                var sameTarget = currentConfig && currentConfig.credential_id === model.credential_id && currentConfig.model === model.model_name;
                var nextConfig = modelConfig(model, sameTarget ? currentConfig : null);
                await scope('patch', '/sessions/' + encodeURIComponent(selectedSession.value), { chat_model_config: nextConfig }, { params: { agent_id: selectedAgent.value } });
                var row = sessions.value.find(function (item) { return sessionId(item) === selectedSession.value; });
                if (row) sessionConfig(row).chat_model_config = nextConfig;
                if (!silent) MessagePlugin.success('模型已切换');
            }
            async function selectModel(value) {
                selectedModelKey.value = value || ''; resetTurnReasoning();
                if (attachments.value.length && selectedModel() && !selectedModel().support_vision) MessagePlugin.warning('当前模型不支持图片，请移除图片或切换视觉模型');
                try { if (selectedModel() && selectedSession.value) await applyModelToSession(selectedModel(), false); } catch (err) { fail(err, '模型切换失败'); }
            }
            async function createSession(silent) {
                var model = selectedModel();
                if (!silent) resetTurnReasoning();
                if (!selectedAgent.value || !model) { MessagePlugin.warning(model ? '对话服务尚未就绪' : '请先启用并选择模型'); return ''; }
                try {
                    var result = await scope('post', '/sessions/', { agent_id: selectedAgent.value, name: '新对话', chat_model_config: modelConfig(model) });
                    selectedSession.value = result && result.session_id || '';
                    await loadSessions();
                    if (!silent) MessagePlugin.success('新对话已创建');
                    return selectedSession.value;
                } catch (err) { fail(err, '新对话创建失败'); return ''; }
            }
            async function selectSession(value) {
                selectedSession.value = value || ''; closeStream(); syncModelFromSession(); resetTurnReasoning();
                if (selectedSession.value) await normalizeSessionModel();
                var pending = await loadMessages(); if (selectedSession.value) await openStream(selectedSession.value, true);
                await rejectLegacyConfirmations(pending || []);
            }
            var deleteSession = window.HuiYanAiChatSessionActions.createDeleteHandler({
                sessions: sessions, selectedSession: selectedSession, chatItems: chatItems, running: running, deletingSession: deletingSession, replyError: replyError,
                message: MessagePlugin, sessionId: sessionId, closeStream: closeStream, selectSession: selectSession, fail: fail,
                remove: function (target) { return scope('delete', '/sessions/' + encodeURIComponent(target), { params: { agent_id: selectedAgent.value } }); }, exists: function (target) { return scope('get', '/sessions/', { params: { agent_id: selectedAgent.value } }).then(function (result) { return list(result, 'sessions').some(function (row) { return sessionId(row) === target; }); }); },
                restoreStream: function (target) { openStream(target, true).catch(function () {}); }
            });

            async function loadUploadPolicy() {
                try {
                    if (window.UploadPolicyClient) uploadLimits.value = await window.UploadPolicyClient.getImageLimits();
                    else uploadLimits.value = await request.get('/upload/limits').then(body);
                    uploadPolicyError.value = '';
                }
                catch (err) { uploadPolicyError.value = '图片上传策略读取失败，暂时无法选择图片'; }
            }
            function imageAccept() {
                if (!uploadLimits.value) return 'image/*';
                if (window.UploadPolicyClient) return window.UploadPolicyClient.toAccept(uploadLimits.value.image_extensions);
                return uploadLimits.value.image_extensions.map(function (ext) { return '.' + ext; }).join(',');
            }
            var canUseImages = computed(function () { return Boolean(selectedModel() && selectedModel().support_vision && uploadLimits.value); });
            var reasoningSupported = computed(function () { return Boolean(selectedModel() && selectedModel().support_reasoning); });
            var reasoningEffortOptions = computed(function () { return selectedModel() ? selectedModel().reasoning_efforts : []; });
            var reasoningControlsDisabled = computed(function () { return sending.value || running.value; });
            function resetTurnReasoning() { var model = selectedModel(), options = model ? model.reasoning_efforts : []; reasoningEffort.value = model && model.default_reasoning_effort || (options[0] && options[0].value) || ''; }
            var imageActions = computed(function () { return [{ name: 'uploadImage', uploadProps: {
                multiple: true, accept: imageAccept(), disabled: !canUseImages.value
            }, action: function (data) { selectFiles(data); } }]; });
            var attachmentProps = computed(function () { return { items: attachments.value, overflow: 'scrollX' }; });
            var hasAttachmentError = computed(function () { return attachments.value.some(function (item) { return item.status === 'fail' || !item.modelUrl; }); });
            var composerWarning = computed(function () {
                if (selectedModel() && !selectedModel().support_vision) return '当前模型不支持图片输入';
                if (uploadPolicyError.value) return uploadPolicyError.value;
                if (replyError.value) return replyError.value + '，可修改内容后重试';
                return '';
            });
            var sendDisabled = computed(function () {
                return sending.value || running.value || hasAttachmentError.value || (!draft.value.trim() && !attachments.value.length)
                    || (attachments.value.length && !canUseImages.value);
            });
            function filePayload(value) { return value && value.files ? value : value && value.detail && value.detail.files ? value.detail : { files: [] }; }
            async function uploadAttachment(item, file) {
                var form = new FormData(); form.append('file', file);
                try {
                    var uploaded = await scope('post', '/upload/image', form);
                    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
                    item.remoteUrl = new URL(uploaded.url, window.location.origin).href;
                    item.modelUrl = uploaded.model_url || item.remoteUrl;
                    item.url = item.remoteUrl; item.name = uploaded.name || item.name; item.size = uploaded.size || item.size;
                    item.mimeType = uploaded.mime_type || file.type; item.status = 'success';
                    attachments.value = attachments.value.slice();
                } catch (err) {
                    item.status = 'fail'; item.error = errorText(err, '图片上传失败'); attachments.value = attachments.value.slice();
                    fail(err, '图片上传失败');
                }
            }
            async function selectFiles(value) {
                if (!canUseImages.value) { MessagePlugin.warning(composerWarning.value || '当前模型不支持图片'); return; }
                var files = Array.from(filePayload(value).files || []);
                if (attachments.value.length + files.length > MAX_IMAGES) { MessagePlugin.warning('每次最多上传 4 张图片'); files = files.slice(0, MAX_IMAGES - attachments.value.length); }
                var allowed = uploadLimits.value.image_extensions, maxBytes = uploadLimits.value.image_max_size_mb * 1024 * 1024, jobs = [];
                files.forEach(function (file) {
                    var ext = String(file.name || '').split('.').pop().toLowerCase();
                    if (allowed.indexOf(ext) === -1 || file.size > maxBytes) { MessagePlugin.warning('图片格式或大小不符合上传策略'); return; }
                    var item = { key: 'image-' + Date.now() + '-' + Math.random(), name: file.name, size: file.size, fileType: 'image', status: 'progress', previewUrl: URL.createObjectURL(file) };
                    item.url = item.previewUrl; attachments.value.push(item); jobs.push(uploadAttachment(item, file));
                });
                if (attachments.value.length && !draft.value.trim()) draft.value = IMAGE_ONLY_MARKER;
                await Promise.all(jobs);
            }
            function removeAttachment(value) {
                var target = value && value.detail || value && value.item || value;
                var index = attachments.value.findIndex(function (item) { return item === target || item.key === target.key || item.name === target.name; });
                if (index >= 0) { var removed = attachments.value.splice(index, 1)[0]; if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl); }
                if (!attachments.value.length && draft.value === IMAGE_ONLY_MARKER) draft.value = '';
            }

            function sendText(value) {
                if (typeof value === 'string' || typeof value === 'number') return String(value).split(IMAGE_ONLY_MARKER).join('').trim();
                var detail = value && value.detail;
                var text = typeof detail === 'string' ? detail : (detail && typeof detail.value === 'string' ? detail.value : draft.value);
                return String(text || '').split(IMAGE_ONLY_MARKER).join('').trim();
            }
            async function ensureSessionModel() {
                var row = sessions.value.find(function (item) { return sessionId(item) === selectedSession.value; }), model = selectedModel();
                if (model && !sameModel(sessionConfig(row).chat_model_config, modelConfig(model, sessionConfig(row).chat_model_config))) await applyModelToSession(model, true);
                return Boolean(model);
            }
            async function sendMessage(value) {
                var text = sendText(value), readyImages = attachments.value.filter(function (item) { return item.status === 'success' && item.modelUrl; });
                var turnThinking = reasoningSupported.value, turnEffort = turnThinking ? reasoningEffort.value || null : null;
                if (sending.value || running.value || (!text && !readyImages.length)) return;
                if (!selectedModel()) { MessagePlugin.warning('请先选择模型'); return; }
                if (attachments.value.length && (!canUseImages.value || hasAttachmentError.value)) { MessagePlugin.warning('请先处理无法发送的图片'); return; }
                if (!selectedSession.value && !(await createSession(true))) return;
                try {
                    if (!(await ensureSessionModel())) return;
                    await openStream(selectedSession.value, true);
                    sending.value = true; running.value = true; replyError.value = '';
                    var blocks = [];
                    if (text) blocks.push({ type: 'text', text: text });
                    readyImages.forEach(function (item) { blocks.push({ type: 'data', name: item.name || '图片', source: { type: 'url', url: item.modelUrl, media_type: item.mimeType || 'image/jpeg' } }); });
                    chatItems.value.push({ id: 'local-user-' + Date.now(), role: 'user', status: '', replyStatus: 'complete', content: blocks.map(function (block, index) { return messageState.normalizeBlock(block, 'user', index); }).filter(Boolean) });
                    chatItems.value.push({ id: 'pending-' + Date.now(), role: 'assistant', status: 'pending', content: [], pendingPlaceholder: true });
                    await scrollBottom();
                    await scope('post', '/chat/turn', { agent_id: selectedAgent.value, session_id: selectedSession.value, input: { name: 'user', role: 'user', content: blocks }, thinking_enable: turnThinking, reasoning_effort: turnEffort });
                    resetTurnReasoning();
                    draft.value = ''; attachments.value.forEach(function (item) { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); }); attachments.value = [];
                } catch (err) {
                    chatItems.value = chatItems.value.filter(function (item) { return !item.pendingPlaceholder; });
                    markStreamError(errorText(err, '消息发送失败，请重试')); fail(err, '消息发送失败');
                } finally { sending.value = false; }
            }
            async function interruptMessage() {
                if (!selectedSession.value || !running.value) return;
                sending.value = true;
                try {
                    await scope('post', '/sessions/' + encodeURIComponent(selectedSession.value) + '/interrupt', null, { params: { agent_id: selectedAgent.value } });
                    await new Promise(function (resolve) { window.setTimeout(resolve, 500); });
                    if (running.value) await loadMessages(false);
                } catch (err) { fail(err, '停止生成失败'); }
                finally { sending.value = false; }
            }

            async function refreshAll() {
                loading.value = true; error.value = ''; replyError.value = ''; closeStream();
                try { await Promise.all([loadModelCatalog(), loadUploadPolicy()]); await ensureRuntimeAgent(); await loadSessions(); }
                catch (err) { error.value = '对话资源读取失败，请刷新重试'; fail(err, error.value); }
                finally { loading.value = false; }
            }
            function isToolBlock(block) { return messageState.isToolBlock(block); }
            var markdownProps = { engine: 'marked', options: {} };

            onMounted(refreshAll);
            onBeforeUnmount(function () { closeStream(); attachments.value.forEach(function (item) { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); }); });
            return {
                loading: loading, modelsLoading: modelsLoading, sending: sending, running: running, error: error, sessions: sessions, chatItems: chatItems,
                selectedSession: selectedSession, selectedModelKey: selectedModelKey, draft: draft, deletingSession: deletingSession, messageList: messageList,
                modelOptions: modelOptions, attachmentProps: attachmentProps, imageActions: imageActions, sendDisabled: sendDisabled, composerWarning: composerWarning, markdownProps: markdownProps,
                reasoningEffort: reasoningEffort, reasoningSupported: reasoningSupported, reasoningEffortOptions: reasoningEffortOptions, reasoningControlsDisabled: reasoningControlsDisabled,
                refreshAll: refreshAll, createSession: function () { return createSession(false); }, selectSession: selectSession, deleteSession: deleteSession, selectModel: selectModel, sendMessage: sendMessage, interruptMessage: interruptMessage, selectFiles: selectFiles, removeAttachment: removeAttachment,
                sessionId: sessionId, sessionName: sessionName, sessionTime: sessionTime, isToolBlock: isToolBlock,
                toolTitle: messageState.toolTitle, toolExpandValue: messageState.toolExpandValue,
                setBlockCollapsed: messageState.setBlockCollapsed, setToolExpanded: messageState.setToolExpanded
            };
        }
    });
})();
