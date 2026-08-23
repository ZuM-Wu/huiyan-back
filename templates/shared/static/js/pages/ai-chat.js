/**
 * AI 对话页（管理员端）
 *
 * 布局：左侧会话列表 + 右侧消息流（自建聊天 UI 组件）
 * 关键机制：
 * - SSE 流式：fetch + getReader 解析统一事件信封（AiChatUtils.readSseStream）
 * - 消息段模型：每条 assistant 消息的 content 为段数组
 *   {type:'reasoning'|'tool'|'markdown', ...}，按事件类型增量写入（AiChatUtils.handleEvent）
 * - 技能双通道：输入框首字符 "/" 弹技能面板（按名称过滤，Enter 选中首项）；
 *   发送区工具栏技能按钮弹同一面板；选中后以可移除标签展示
 * - 共享组件：AiMessageList / AiChatSender 等由 ai-*-.js 组件文件注册到 HuiYanComponents
 */

(function () {
HuiYan.createPage({
    setup() {
        const { ref, computed, watch, onMounted, nextTick } = Vue;
        const { MessagePlugin, DialogPlugin } = TDesign;
        const Utils = window.AiChatUtils;

        // 封装 DialogPlugin.confirm 为 Promise（TDesign 原生是回调式）
        function confirmAsync(options) {
            return new Promise(function (resolve) {
                var instance = DialogPlugin.confirm({
                    header: options.header,
                    body: options.body,
                    onConfirm: function () { instance.destroy(); resolve(true); },
                    onClose: function () { instance.destroy(); resolve(false); },
                    onCancel: function () { instance.destroy(); resolve(false); }
                });
            });
        }

        /* ===== 会话列表 ===== */
        const conversations = ref([]);
        const conversationId = ref(0);

        async function fetchConversations() {
            try {
                const res = await request.get('/ai/conversations', { params: { page: 1, page_size: 50 } });
                conversations.value = (res.data.data || {}).list || [];
            } catch (e) { /* 列表失败静默，不阻断聊天 */ }
        }

        function newConversation() {
            if (isStreaming.value) { MessagePlugin.warning('请等待当前回复结束'); return; }
            conversationId.value = 0;
            chatList.value = [];
            selectedSkill.value = null;
            uploadedImages.value = [];
        }

        async function openConversation(conv) {
            if (isStreaming.value) { MessagePlugin.warning('请等待当前回复结束'); return; }
            conversationId.value = conv.id;
            uploadedImages.value = [];
            try {
                const res = await request.get('/ai/conversations/' + conv.id + '/messages',
                    { params: { page: 1, page_size: 200 } });
                const data = res.data.data || {};
                chatList.value = [];
                await nextTick();
                chatList.value = Utils.mapHistory(data.list || []);
                scrollToBottom();
            } catch (e) {
                MessagePlugin.error('加载历史消息失败');
            }
        }

        async function removeConversation(conv) {
            var yes = await confirmAsync({ header: '删除会话', body: '确定删除会话「' + (conv.title || '未命名会话') + '」吗？' });
            if (!yes) return;
            try {
                await request.delete('/ai/conversations/' + conv.id);
                MessagePlugin.success('会话已删除');
                if (conv.id === conversationId.value) newConversation();
                fetchConversations();
            } catch (e) {
                MessagePlugin.error(e.response?.data?.detail || '删除失败');
            }
        }

        /* ===== 模型与技能 ===== */
        const models = ref([]);
        const selectedModel = ref('');
        const modelReady = ref(false);
        const modelMessage = ref('模型接口尚未完成配置');
        let modelCapabilities = {};
        const skills = ref([]);
        const selectedSkill = ref(null);
        const skillPanelVisible = ref(false);
        const skillFilter = ref('');
        const uploadedImages = ref([]);

        const filteredSkills = computed(function () {
            const kw = skillFilter.value.trim().toLowerCase();
            if (!kw) return skills.value;
            return skills.value.filter(function (s) { return s.name.toLowerCase().indexOf(kw) >= 0; });
        });

        // 面板按内置能力与插件能力分组，兼容后端未来返回的 group/category 字段。
        const skillGroups = computed(function () {
            var groups = [];
            filteredSkills.value.forEach(function (skill) {
                var rawGroup = String(skill.group || skill.category || '').toLowerCase();
                // 当前接口使用 name 承载技能标识，兼容旧版 id 字段。
                var skillId = String(skill.id || '');
                var skillName = String(skill.name || '');
                var isPlugin = rawGroup === 'plugin' || rawGroup === 'plugins'
                    || skillId.indexOf('ext__') === 0 || skillName.indexOf('ext__') === 0;
                var groupName = isPlugin ? '插件' : '内置技能';
                var group = groups.find(function (item) { return item.name === groupName; });
                if (!group) {
                    group = { name: groupName, items: [] };
                    groups.push(group);
                }
                group.items.push(skill);
            });
            return groups;
        });

        async function fetchModels() {
            try {
                const res = await request.get('/ai/models');
                const data = res.data.data || {};
                models.value = data.list || [];
                modelReady.value = data.ready === true && models.value.length > 0;
                modelMessage.value = data.message || '模型接口尚未完成配置';
                modelCapabilities = {};
                models.value.forEach(function (m) { modelCapabilities[m.name] = m; });
                var defaultModel = data.default_model || '';
                selectedModel.value = models.value.some(function (m) {
                    return m.name === defaultModel;
                }) ? defaultModel : (models.value[0] || {}).name || '';
            } catch (e) {
                models.value = [];
                selectedModel.value = '';
                modelReady.value = false;
                modelMessage.value = '模型状态读取失败，请前往 AI 设置检查';
            }
        }

        async function fetchSkills() {
            try {
                const res = await request.get('/ai/skills');
                skills.value = (res.data.data || {}).list || [];
            } catch (e) { /* 静默 */ }
        }

        function toggleSkillPanel() {
            skillFilter.value = '';
            if (!skillPanelVisible.value) fetchSkills();
            skillPanelVisible.value = !skillPanelVisible.value;
        }

        function selectSkill(skill) {
            selectedSkill.value = skill;
            skillPanelVisible.value = false;
            if (senderValue.value.charAt(0) === '/') senderValue.value = '';
        }

        function goAiSettings() {
            window.location.href = '/admin/ai-setting?tab=interfaces';
        }

        /* ===== 发送区（"/" 技能通道监听） ===== */
        const senderValue = ref('');
        watch(senderValue, function (val) {
            if (val.charAt(0) === '/') {
                skillFilter.value = val.slice(1);
                skillPanelVisible.value = true;
            } else if (skillPanelVisible.value) {
                skillFilter.value = '';
                skillPanelVisible.value = false;
            }
        });

        /* ===== 消息流与 SSE ===== */
        const chatList = ref([]);
        const messageListRef = ref(null);
        const loading = ref(false);
        const isStreaming = ref(false);
        const uploadingImages = ref(false);
        let abortController = null;

        // rAF 节流的滚动到底部：多 token 同帧只触发一次 DOM 写入
        var _scrollRafPending = false;
        function scrollToBottom() {
            if (!messageListRef.value || _scrollRafPending) return;
            _scrollRafPending = true;
            requestAnimationFrame(function () {
                _scrollRafPending = false;
                if (messageListRef.value && messageListRef.value.scrollToBottom) {
                    messageListRef.value.scrollToBottom();
                }
            });
        }

        // SSE 事件处理上下文（传给 AiChatUtils.handleEvent）
        var chatCtx = {
            chatList: chatList,
            loading: loading,
            conversationId: conversationId,
            onMeta: function () { fetchConversations(); },
            onError: function (msg) { MessagePlugin.error(msg); },
            onScroll: function () { scrollToBottom(); }
        };

        async function onSend(text) {
            text = (text || '').trim();
            var files = uploadedImages.value || [];
            if (!modelReady.value) {
                MessagePlugin.warning(modelMessage.value || '请先配置可用模型');
                return;
            }
            if (isStreaming.value || uploadingImages.value || (!text && !files.length)) return;
            // "/" 面板打开时 Enter 表示选中首个技能而非发送
            if (skillPanelVisible.value && text.charAt(0) === '/') {
                if (filteredSkills.value.length) selectSkill(filteredSkills.value[0]);
                return;
            }
            skillPanelVisible.value = false;
            var imageUrls = [];
            var attachments = [];
            if (files.length) {
                uploadingImages.value = true;
                try {
                    imageUrls = await Utils.readFilesAsDataUrls(files);
                    attachments = await Utils.uploadImages(
                        files, '/api/admin/v1/upload/image', 'admin_token');
                } catch (e) {
                    MessagePlugin.error(e.message || '图片上传失败');
                    return;
                } finally {
                    uploadingImages.value = false;
                }
            }
            senderValue.value = '';
            uploadedImages.value = [];
            var content = [];
            if (text) content.push({ type: 'text', text: text });
            imageUrls.forEach(function (url) {
                content.push({ type: 'image_url', image_url: { url: url } });
            });
            var userSegments = [];
            if (attachments.length) userSegments.push({ type: 'images', items: attachments });
            userSegments.push({ type: 'markdown', data: text || '已上传图片，请分析图片内容。' });
            chatList.value.push({ role: 'user', name: '我', datetime: '', content: userSegments });
            chatList.value.push({ role: 'assistant', name: 'AI 助手', datetime: '', content: [] });
            loading.value = true;
            isStreaming.value = true;
            scrollToBottom();

            abortController = new AbortController();
            try {
                await Utils.fetchSse('/api/admin/v1/ai/chat', {
                    body: {
                        conversation_id: conversationId.value,
                        content: content,
                        attachments: attachments,
                        model: selectedModel.value || '',
                        skill_id: 0
                    },
                    signal: abortController.signal,
                    onEvent: function (ev) {
                        Utils.handleEvent(ev, chatCtx);
                    }
                });
            } catch (e) {
                if (e.name !== 'AbortError') {
                    MessagePlugin.error(e.message || '发送失败');
                    Utils.handleEvent({ type: 'error', data: { message: e.message || '发送失败' } }, chatCtx);
                }
            } finally {
                loading.value = false;
                isStreaming.value = false;
                abortController = null;
                fetchConversations();
            }
        }

        function onStop() {
            if (abortController) abortController.abort();
        }

        onMounted(function () {
            fetchConversations();
            fetchModels();
            fetchSkills();
        });

        return {
            conversations, conversationId, newConversation, openConversation, removeConversation,
            models, selectedModel, modelReady, modelMessage, skills, selectedSkill,
            skillPanelVisible, filteredSkills, skillGroups,
            toggleSkillPanel, selectSkill, goAiSettings,
            senderValue, chatList, messageListRef, loading, isStreaming, onSend, onStop,
            uploadedImages, uploadingImages
        };
    }
});
})();
