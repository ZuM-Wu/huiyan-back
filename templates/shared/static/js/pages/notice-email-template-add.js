/**
 * 邮件模板创建/编辑页脚本
 *
 * 独立全页面（非弹窗），解决 TinyMCE 在 Dialog 内生命周期异常问题
 * URL 参数 ?id=xxx 时为编辑模式，无 id 为新建模式
 * 使用 SPA 导航（HuiYan.loadPage）跳转，TinyMCE vendor 在 block scripts 中引入
 */
(function () {
HuiYan.createPage({
    setup() {
        const { ref, reactive, onMounted, nextTick } = Vue;

        // ---- 模式判断 ----
        const isEdit = ref(false);
        const templateId = ref(0);
        const saving = ref(false);
        const formRef = ref(null);
        const emailEditor = ref(null);
        const showVarHint = ref(false);

        // ---- 表单数据（邮件模板不绑定插件接口） ----
        const form = reactive({
            name: '',
            subject: '',
            content: '',
            action_key: '',
        });

        // ---- 表单校验规则（视觉设计规范 9.9） ----
        const rules = {
            name: [{ required: true, message: '请输入模板名称', type: 'error' }],
            subject: [{ required: true, message: '请输入邮件主题', type: 'error' }],
        };

        // ---- 变量提示表格 ----
        const varHintColumns = [
            { colKey: 'var', title: '变量', width: 200 },
            { colKey: 'desc', title: '说明' },
        ];
        const varHintList = ref([
            { var: '{code}', desc: '验证码' },
            { var: '{username}', desc: '用户名' },
            { var: '{phone}', desc: '手机号' },
            { var: '{email}', desc: '邮箱' },
            { var: '{system_website_name}', desc: '系统名称' },
            { var: '{area_name}', desc: '产区名称' },
            { var: '{plot_name}', desc: '地块名称' },
            { var: '{time}', desc: '当前时间' },
            { var: '{company}', desc: '公司名' },
        ]);

        // ---- 通知动作列表（下拉选择） ----
        const actionList = ref([]);

        async function fetchActions() {
            try {
                const res = await request.get('/notice/actions/list', { params: { limit: 100 } });
                actionList.value = (res.data.data || res.data).list || [];
            } catch (e) {
                // 静默失败，动作选择为可选字段
            }
        }

        // ---- 加载模板详情（编辑模式） ----
        async function loadTemplate(id) {
            try {
                const res = await request.get('/notice/email-templates/' + id);
                var tpl = res.data.data || res.data;
                Object.assign(form, {
                    name: tpl.name || '',
                    subject: tpl.subject || '',
                    content: tpl.content || '',
                    action_key: tpl.action_key || '',
                });
                // 等 DOM 更新后设置编辑器内容
                nextTick(function () {
                    if (emailEditor.value) {
                        emailEditor.value.setContent(form.content);
                    }
                });
            } catch (e) {
                MessagePlugin.error('加载模板失败');
            }
        }

        // ---- 保存提交 ----
        function onSubmit(params) {
            if (params && params.e) params.e.preventDefault();
            // 校验未通过时错误已内联显示，直接中断
            if (params && params.validateResult !== true) return;
            doSave();
        }

        async function doSave() {
            saving.value = true;
            try {
                // 从 TinyMCE 编辑器获取最新 HTML 内容
                if (emailEditor.value) {
                    form.content = emailEditor.value.getContent();
                }

                if (isEdit.value) {
                    await request.put('/notice/email-templates/' + templateId.value, { ...form });
                } else {
                    await request.post('/notice/email-templates/', { ...form });
                }
                MessagePlugin.success('保存成功');
                // 保存后返回邮件通知列表
                goBack();
            } catch (e) {
                // 请求拦截器统一处理错误提示
            } finally {
                saving.value = false;
            }
        }

        // ---- 返回邮件通知页“模板管理”Tab（SPA 导航） ----
        function goBack() {
            HuiYan.loadPage('/admin/notice-email?tab=templates');
        }

        // ---- 初始化 ----
        onMounted(function () {
            fetchActions();

            // 从 URL 参数判断编辑/新建模式
            var params = new URLSearchParams(window.location.search);
            var id = params.get('id');
            if (id) {
                isEdit.value = true;
                templateId.value = parseInt(id, 10);
                loadTemplate(templateId.value);
            }
        });

        return {
            isEdit, saving, formRef, emailEditor, showVarHint,
            form, rules, actionList,
            varHintColumns, varHintList,
            onSubmit, goBack,
        };
    },
});
})();
