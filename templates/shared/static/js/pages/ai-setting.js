/** AI 资源设置页；四类资源共用后端与 AgentScope 请求门面。 */
(function () {
    var EmptyState = { name: 'AiEmptyState', props: { description: { type: String, default: '暂无数据' } }, template: '<div class="ai-empty__content" role="status"><t-icon name="file" size="24"></t-icon><span>[[ description ]]</span></div>' };

    HuiYan.createPage({
        components: { 't-empty': EmptyState },
        setup: function () {
            var ref = Vue.ref, reactive = Vue.reactive, onMounted = Vue.onMounted, onBeforeUnmount = Vue.onBeforeUnmount;
            var MessagePlugin = TDesign.MessagePlugin, DialogPlugin = TDesign.DialogPlugin;
            var tab = ref('connections'), loading = ref(false), error = ref('');
            var presets = ref([]), connections = ref([]), cards = ref([]), mcps = ref([]), skills = ref([]);
            var agents = ref([]), sessions = ref([]), selectedAgent = ref(''), selectedSession = ref('');
            var selectedSkill = ref(null), availableModels = ref([]);
            var showConnection = ref(false), showCard = ref(false), showMcp = ref(false), showSkillInstall = ref(false);
            var editingConnection = ref(null), editingCard = ref(null), editingMcp = ref(null);
            var skillInput = ref(null), skillUploading = ref(false), skillInstalling = ref(false);
            var connectionForm = reactive({ preset_key: 'deepseek', vendor: 'DeepSeek', vendor_name: 'DeepSeek', provider: 'deepseek_credential', protocol: 'openai_chat_completions', name: '', api_key: '', base_url: '', status: 1 });
            var cardForm = reactive({ connection_id: null, provider: '', model_name: '', label: '', input_types: [], output_types: [], context_size: 32768, output_size: 4096, support_tools: false, support_reasoning: false, parameter_schema: '{}', status: 1 });
            var mcpForm = reactive({ kind: 'custom', name: '', display_name: '', description: '', url: '', headers: '', api_key: '', clear_headers: false, enabled: true });
            var connectionColumns = [{ colKey: 'name', title: '连接名称', minWidth: 150 }, { colKey: 'vendor', title: '厂商', width: 130 }, { colKey: 'protocol', title: '协议', width: 180 }, { colKey: 'base_url', title: 'Base URL', minWidth: 260 }, { colKey: 'status', title: '状态', width: 90 }, { colKey: 'last_test_status', title: '自动检测', width: 130 }, { colKey: 'operation', title: '操作', width: 280, fixed: 'right' }];
            var cardColumns = [{ colKey: 'connection', title: '来源连接', minWidth: 160 }, { colKey: 'label', title: '展示名称', minWidth: 170 }, { colKey: 'model_name', title: '模型 ID', minWidth: 180 }, { colKey: 'context_size', title: '上下文', width: 110 }, { colKey: 'capabilities', title: '能力', minWidth: 220 }, { colKey: 'status', title: '状态', width: 90 }, { colKey: 'operation', title: '操作', width: 150, fixed: 'right' }];
            var mcpColumns = [{ colKey: 'display_name', title: 'MCP 名称', minWidth: 180 }, { colKey: 'url', title: '地址', minWidth: 260 }, { colKey: 'description', title: '描述', minWidth: 220 }, { colKey: 'enabled', title: '状态', width: 90 }, { colKey: 'operation', title: '操作', width: 220, fixed: 'right' }];
            var skillColumns = [{ colKey: 'name', title: 'Skill 名称', minWidth: 180 }, { colKey: 'description', title: '描述', minWidth: 260 }, { colKey: 'enabled', title: '状态', width: 90 }, { colKey: 'operation', title: '操作', width: 190 }];

            function body(response) { var value = response && response.data; if (value && value.status !== undefined && Object.prototype.hasOwnProperty.call(value, 'data')) return value.data; return value === undefined ? response : value; }
            function call(method, path, data, config) { return request[method].call(request, path, data, config).then(body); }
            function scope(method, path, data, config) { return request.agentScope[method].call(request.agentScope, path, data, config).then(body); }
            function resource(method, path, data, config) { return call(method, '/ai-resources' + path, data, config); }
            function fail(err, fallback) { var detail = err && err.response && err.response.data && (err.response.data.detail || err.response.data.msg); MessagePlugin.error(detail || fallback); }
            function list(value, key) { if (Array.isArray(value)) return value; return (value && value[key]) || (value && value.items) || []; }
            function agentId(row) { return row && (row.id || (row.data && row.data.id) || (row.agent && row.agent.id)); }
            function agentName(row) { return (row && row.data && row.data.name) || (row && row.name) || '未命名 Agent'; }
            function sessionRecord(row) { return (row && row.session) || row || {}; }
            function sessionId(row) { return sessionRecord(row).id || ''; }
            function sessionName(row) { return sessionRecord(row).config && sessionRecord(row).config.name || sessionId(row) || '未命名 Session'; }
            function connectionName(id) { var row = connections.value.find(function (item) { return item.id === id; }); return row ? (row.name || row.vendor) : '未知连接'; }
            function connectionCount(key) { return connections.value.filter(function (item) { return item.preset_key === key; }).length; }
            function connectionHealth(key) {
                var rows = connections.value.filter(function (item) { return item.preset_key === key && item.status === 1; });
                if (!rows.length) return { theme: 'default', text: '暂无启用连接' };
                var failed = rows.filter(function (item) { return item.last_test_status === 2; }).length;
                if (failed) return { theme: 'danger', text: failed + ' 个连接异常' };
                if (rows.every(function (item) { return item.last_test_status === 1; })) return { theme: 'success', text: '全部正常' };
                return { theme: 'warning', text: '等待首次检测' };
            }
            function testTime(row) { return row.last_test_at ? String(row.last_test_at).replace('T', ' ').slice(0, 16) : '尚未检测'; }
            function protocolLabel(value) { if (value === 'openai_chat_completions') return 'OpenAI Chat Completions'; if (value === 'openai_responses') return 'OpenAI Responses'; return value || '未设置'; }
            function selectedConnectionPreset() { return presets.value.find(function (item) { return item.preset_key === connectionForm.preset_key; }) || null; }
            function connectionProtocolOptions() { var selected = selectedConnectionPreset(); return selected && selected.supported_protocols || []; }
            function connectionProtocolLocked() { var selected = selectedConnectionPreset(); return Boolean(editingConnection.value || !selected || selected.protocol_locked); }
            function selectConnectionProtocol(value) { var option = connectionProtocolOptions().find(function (item) { return item.value === value; }); if (option) connectionForm.provider = option.provider; }

            async function loadAll() {
                loading.value = true; error.value = '';
                try { var result = await Promise.all([resource('get', '/connection-presets'), resource('get', '/connections'), resource('get', '/model-cards'), resource('get', '/mcps'), resource('get', '/skills')]); presets.value = list(result[0], 'list'); connections.value = list(result[1], 'list'); cards.value = list(result[2], 'list'); mcps.value = list(result[3], 'list'); skills.value = list(result[4], 'list'); }
                catch (err) { error.value = '资源读取失败，请刷新重试'; fail(err, error.value); }
                finally { loading.value = false; }
            }
            async function loadConnectionHealth() {
                try { connections.value = list(await resource('get', '/connections'), 'list'); }
                catch (err) { /* 页面定时刷新失败时保留最近一次可用状态。 */ }
            }
            function handleTabChange() { return undefined; }
            async function loadSkillAgents() { try { agents.value = list(await scope('get', '/agent/'), 'agents'); if (agents.value[0]) { selectedAgent.value = agentId(agents.value[0]); await loadSkillSessions(); } } catch (err) { fail(err, 'Agent 读取失败'); } }
            async function loadSkillSessions() { if (!selectedAgent.value) { sessions.value = []; selectedSession.value = ''; return; } try { sessions.value = list(await scope('get', '/sessions/', { params: { agent_id: selectedAgent.value } }), 'sessions'); selectedSession.value = sessions.value[0] ? sessionId(sessions.value[0]) : ''; } catch (err) { sessions.value = []; selectedSession.value = ''; fail(err, 'Session 读取失败'); } }
            function openConnection(preset, row) { editingConnection.value = row || null; var selected = preset || presets.value.find(function (item) { return item.preset_key === (row && row.preset_key); }) || presets.value[0]; var custom = selected && selected.preset_key === 'custom_openai'; Object.assign(connectionForm, { preset_key: row ? row.preset_key : (selected ? selected.preset_key : 'custom_openai'), vendor: row ? row.vendor : (selected ? selected.vendor : '自定义供应商'), vendor_name: row ? (row.vendor_name || row.vendor) : (custom ? '' : (selected ? selected.vendor : '')), provider: row ? row.provider : (selected ? selected.provider : 'openai_credential'), protocol: row ? row.protocol : (selected ? selected.protocol : 'openai_chat_completions'), name: row ? row.name : '', api_key: '', base_url: row ? row.base_url : (selected ? selected.base_url : ''), status: row ? row.status : 1 }); showConnection.value = true; }
            function selectConnectionPreset(value) { var selected = presets.value.find(function (item) { return item.preset_key === value; }); if (!selected || editingConnection.value) return; Object.assign(connectionForm, { vendor: selected.vendor, vendor_name: selected.preset_key === 'custom_openai' ? '' : selected.vendor, provider: selected.provider, protocol: selected.protocol, base_url: selected.base_url || '' }); }
            function editConnection(row) { openConnection(null, row); }
            async function saveConnection() { try { var data = { name: connectionForm.name, base_url: connectionForm.base_url, status: connectionForm.status }; if (connectionForm.api_key) data.api_key = connectionForm.api_key; if (!editingConnection.value) { data.preset_key = connectionForm.preset_key; data.provider = connectionForm.provider; data.protocol = connectionForm.protocol; data.vendor_name = connectionForm.vendor_name; } await resource(editingConnection.value ? 'patch' : 'post', '/connections' + (editingConnection.value ? '/' + editingConnection.value.id : ''), data); showConnection.value = false; MessagePlugin.success('供应商连接已保存'); await loadAll(); } catch (err) { fail(err, '供应商连接保存失败'); } }
            async function connectionAction(path, method, message, data) {
                try {
                    var result = await resource(method, path, data);
                    await loadAll();
                    if (path.indexOf('/test') !== -1 && result && result.success === false) {
                        MessagePlugin.error(result.message || message + '失败');
                        return;
                    }
                    MessagePlugin.success(message + '成功');
                } catch (err) { fail(err, message + '失败'); }
            }
            function resetAvailableModels() { availableModels.value = []; cardForm.model_name = ''; cardForm.label = ''; }
            async function refreshCardModels() { if (!cardForm.connection_id) { MessagePlugin.warning('请先选择来源连接'); return; } try { var result = await resource('post', '/connections/' + cardForm.connection_id + '/models/refresh'); availableModels.value = result.models || []; MessagePlugin.success('模型列表已刷新'); } catch (err) { availableModels.value = []; fail(err, '模型刷新失败'); } }
            function selectCardModel(value) { var model = availableModels.value.find(function (item) { return item.model_name === value; }); if (!model) return; Object.assign(cardForm, { label: model.label || model.model_name, input_types: model.input_types || [], output_types: model.output_types || [], context_size: model.context_size || 32768, output_size: model.output_size || 4096, support_tools: !!model.support_tools, support_reasoning: !!model.support_reasoning, parameter_schema: JSON.stringify(model.parameter_schema || {}, null, 2) }); }
            function openCard() { if (!connections.value.length) { MessagePlugin.warning('请先建立供应商连接'); return; } editingCard.value = null; availableModels.value = []; Object.assign(cardForm, { connection_id: null, provider: '', model_name: '', label: '', input_types: [], output_types: [], context_size: 32768, output_size: 4096, support_tools: false, support_reasoning: false, parameter_schema: '{}', status: 1 }); showCard.value = true; }
            function editCard(row) { editingCard.value = row; availableModels.value = [{ model_name: row.model_name, label: row.label || row.model_name }]; Object.assign(cardForm, row, { input_types: row.input_types || [], output_types: row.output_types || [], parameter_schema: JSON.stringify(row.parameter_schema || {}, null, 2) }); showCard.value = true; }
            function cardPayload() {
                var data = { label: cardForm.label, input_types: cardForm.input_types || [], output_types: cardForm.output_types || [], context_size: cardForm.context_size, output_size: cardForm.output_size, support_tools: cardForm.support_tools, support_reasoning: cardForm.support_reasoning, parameter_schema: JSON.parse(cardForm.parameter_schema || '{}'), status: cardForm.status };
                if (!editingCard.value) { data.connection_id = cardForm.connection_id; data.model_name = cardForm.model_name; }
                return data;
            }
            async function saveCard() { try { var data = cardPayload(); await resource(editingCard.value ? 'patch' : 'post', '/model-cards' + (editingCard.value ? '/' + editingCard.value.id : ''), data); showCard.value = false; MessagePlugin.success('ModelCard 已保存'); await loadAll(); } catch (err) { if (err instanceof SyntaxError) MessagePlugin.warning('参数 Schema 必须是 JSON 对象'); else fail(err, 'ModelCard 保存失败'); } }
            function editMcp(row) { editingMcp.value = row; Object.assign(mcpForm, { kind: row.name === 'huiyan_system_mcp' ? 'system' : 'custom', name: row.name, display_name: row.display_name || '', description: row.description || '', url: row.url || '', headers: '', api_key: '', clear_headers: false, enabled: row.enabled !== false }); showMcp.value = true; }
            function openMcp(kind) { editingMcp.value = null; var system = kind === 'system'; Object.assign(mcpForm, { kind: system ? 'system' : 'custom', name: system ? 'huiyan_system_mcp' : '', display_name: system ? '慧眼护农系统 MCP' : '', description: system ? '慧眼护农农业主链 Tools、Resources 与 Prompts' : '', url: system ? window.location.origin.replace(/\/$/, '') + '/mcp/' : '', headers: '', api_key: '', clear_headers: false, enabled: true }); showMcp.value = true; }
            async function saveMcp() {
                try {
                    var data = { display_name: mcpForm.display_name, description: mcpForm.description, url: mcpForm.url, enabled: mcpForm.enabled };
                    var apiKey = mcpForm.api_key.trim();
                    if (mcpForm.kind === 'system' && !editingMcp.value && !apiKey) { MessagePlugin.warning('请输入个人 API Key'); return; }
                    if (apiKey) data.headers = { Authorization: 'Bearer ' + apiKey };
                    else if (editingMcp.value && mcpForm.clear_headers) data.headers = {};
                    else if (mcpForm.kind === 'custom' && mcpForm.headers.trim()) data.headers = JSON.parse(mcpForm.headers);
                    else if (!editingMcp.value) data.headers = {};
                    if (!editingMcp.value) data.name = mcpForm.name;
                    await resource(editingMcp.value ? 'patch' : 'post', '/mcps' + (editingMcp.value ? '/' + editingMcp.value.id : ''), data);
                    showMcp.value = false; MessagePlugin.success('MCP 已保存'); await loadAll();
                } catch (err) { if (err instanceof SyntaxError) MessagePlugin.warning('Headers 必须是 JSON 对象'); else fail(err, 'MCP 保存失败'); }
            }
            function remove(path, label) { var instance = DialogPlugin.confirm({ header: '确认删除', body: '删除后不可恢复，是否继续？', onConfirm: async function () { instance.destroy(); try { await resource('delete', path); MessagePlugin.success(label + '已删除'); await loadAll(); } catch (err) { fail(err, label + '删除失败'); } }, onCancel: function () { instance.destroy(); } }); }
            function handleSkillFiles(files) { if (files && files.length) uploadSkill(Array.prototype.map.call(files, function (item) { return item.raw || item; })); }
            async function uploadSkill(files) { var form = new FormData(); form.append('manifest', JSON.stringify({ entries: files.map(function (file) { return { path: file.webkitRelativePath || file.name, size: file.size }; }) })); files.forEach(function (file) { form.append('files', file, file.webkitRelativePath || file.name); }); skillUploading.value = true; try { await resource('post', '/skills/upload', form, { skipAutoError: true }); MessagePlugin.success('公共 Skill 已上传'); await loadAll(); } catch (err) { fail(err, 'Skill 上传失败'); } finally { skillUploading.value = false; if (skillInput.value) skillInput.value.value = ''; } }
            function openSkillInstall(row) { selectedSkill.value = row; selectedAgent.value = ''; selectedSession.value = ''; sessions.value = []; showSkillInstall.value = true; loadSkillAgents(); }
            async function installSkill() { if (!selectedSkill.value || !selectedAgent.value || !selectedSession.value) { MessagePlugin.warning('请选择 Agent 和 Session'); return; } skillInstalling.value = true; try { await resource('post', '/skills/' + encodeURIComponent(selectedSkill.value.id) + '/install', { agent_id: selectedAgent.value, session_id: selectedSession.value }); showSkillInstall.value = false; MessagePlugin.success('Skill 已安装到会话'); } catch (err) { fail(err, 'Skill 安装失败'); } finally { skillInstalling.value = false; } }
            async function removeSharedSkill(id) { try { await resource('delete', '/skills/' + encodeURIComponent(id)); MessagePlugin.success('公共 Skill 已删除'); await loadAll(); } catch (err) { fail(err, 'Skill 删除失败'); } }

            var healthTimer = null;
            onMounted(function () { loadAll(); healthTimer = window.setInterval(loadConnectionHealth, 60000); });
            onBeforeUnmount(function () { if (healthTimer) window.clearInterval(healthTimer); });
            return { tab, loading, error, presets, connections, cards, mcps, skills, agents, sessions, selectedAgent, selectedSession, availableModels, selectedSkill, connectionColumns, cardColumns, mcpColumns, skillColumns, showConnection, showCard, showMcp, showSkillInstall, editingConnection, editingCard, editingMcp, connectionForm, cardForm, mcpForm, skillInput, skillUploading, skillInstalling, loadAll, handleTabChange, loadSkillAgents, loadSkillSessions, selectConnectionPreset, selectConnectionProtocol, connectionProtocolOptions, connectionProtocolLocked, openConnection, editConnection, saveConnection, connectionAction, connectionCount, connectionHealth, testTime, connectionName, protocolLabel, resetAvailableModels, refreshCardModels, selectCardModel, openCard, editMcp, saveMcp, openMcp, editCard, saveCard, remove, handleSkillFiles, openSkillInstall, installSkill, removeSharedSkill, agentId, agentName, sessionId, sessionName };
        }
    });
})();
