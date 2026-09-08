/**
 * 农业知识库插件 — 后台管理页前端逻辑
 *
 * 由 knowledge.html 内联脚本等价外提（降低模板单文件有效行数至 400 以内）。
 * 与 file_download.js 同范式：承载于共享 js/pages 目录，经
 * 由插件 manifest 通过 /plugin-assets/knowledge/knowledge.js 引入。页面无 Jinja2
 * 变量注入，无需数据桥接。
 *
 * 功能：
 * - 分类树 CRUD（大类/子类）、条目分页表格与增删改
 * - 批量新增条目（共享分类/作物 + 多行标题）
 * - 条目典型图片复用全局 image-upload 组件，imageTemp 中转追加到 images 数组
 *
 * 勘误审核已迁至独立页 knowledge_corrections.js。
 */
(function () {
    const { ref, reactive, computed, watch, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'knowledge', page: 'knowledge',
        setup() {
            // ---------------- 分类树 ----------------
            const categories = ref([]);
            const currentCategoryId = ref(0);
            const categoryOptions = computed(() => {
                const opts = [];
                categories.value.forEach((root) => {
                    opts.push({ label: root.name, value: root.id });
                    (root.children || []).forEach((ch) => {
                        opts.push({ label: '　└ ' + ch.name, value: ch.id });
                    });
                });
                return opts;
            });
            const categoryNameMap = ref({});
            const categoryName = (id) => categoryNameMap.value[id] || '未分类';

            const fetchCategories = () => {
                request.get('/knowledge/categories').then((res) => {
                    categories.value = (res.data.data && res.data.data.list) || [];
                    const map = {};
                    categories.value.forEach((root) => {
                        map[root.id] = root.name;
                        (root.children || []).forEach((ch) => { map[ch.id] = ch.name; });
                    });
                    categoryNameMap.value = map;
                }).catch(() => {});
            };

            const selectCategory = (id) => {
                currentCategoryId.value = id;
                pagination.current = 1;
                fetchEntries();
            };

            const rootMenu = () => ([
                { content: '添加子类', value: 'addChild' },
                { content: '编辑', value: 'edit' },
                { content: '删除', value: 'delete', theme: 'error' }
            ]);
            const childMenu = () => ([
                { content: '编辑', value: 'edit' },
                { content: '删除', value: 'delete', theme: 'error' }
            ]);

            const onCatMenu = (item, node, parentId) => {
                const action = item.value !== undefined ? item.value : (item.data && item.data.value);
                if (action === 'addChild') { openCatCreate(node.id); }
                else if (action === 'edit') { openCatEdit(node); }
                else if (action === 'delete') { removeCategory(node); }
            };

            // 分类弹窗
            const catFormVisible = ref(false);
            const catFormTitle = ref('新建分类');
            const catEditingId = ref(0);
            const catForm = reactive({ name: '', parent_id: 0, sort_order: 0, status: 1 });
            const catFormRef = ref(null);
            const catRules = {
                name: [{ required: true, message: '请输入分类名称', type: 'error' }]
            };

            const openCatCreate = (parentId) => {
                catEditingId.value = 0;
                catFormTitle.value = parentId ? '新建子类' : '新建大类';
                catForm.name = '';
                catForm.parent_id = parentId;
                catForm.sort_order = 0;
                catForm.status = 1;
                catFormVisible.value = true;
            };
            const openCatEdit = (node) => {
                catEditingId.value = node.id;
                catFormTitle.value = '编辑分类';
                catForm.name = node.name;
                catForm.parent_id = node.parent_id;
                catForm.sort_order = node.sort_order || 0;
                catForm.status = node.status;
                catFormVisible.value = true;
            };
            const submitCategory = () => {
                if (!catFormRef.value) return;
                catFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    const body = { name: catForm.name.trim(), sort_order: catForm.sort_order };
                    let req;
                    if (catEditingId.value) {
                        body.status = catForm.status;
                        req = request.put('/knowledge/categories/' + catEditingId.value, body);
                    } else {
                        body.parent_id = catForm.parent_id;
                        req = request.post('/knowledge/categories', body);
                    }
                    req.then(() => {
                        MessagePlugin.success(catEditingId.value ? '更新成功' : '创建成功');
                        catFormVisible.value = false;
                        fetchCategories();
                    }).catch(() => {});
                });
            };
            const removeCategory = (node) => {
                request.delete('/knowledge/categories/' + node.id).then(() => {
                    MessagePlugin.success('删除成功');
                    if (currentCategoryId.value === node.id) { selectCategory(0); }
                    fetchCategories();
                }).catch(() => {});
            };

            // ---------------- 条目表格 ----------------
            const tableData = ref([]);
            const loading = ref(false);
            const searchKeyword = ref('');
            const cropKeyword = ref('');
            const pagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const columns = [
                { colKey: 'title', title: '标题', minWidth: 180 },
                { colKey: 'category_id', title: '分类', width: 130, cell: 'category_id' },
                { colKey: 'crop', title: '适用作物', width: 140 },
                { colKey: 'view_count', title: '浏览', width: 80 },
                { colKey: 'actions', title: '操作', width: 130, cell: 'actions', className: 'kb-td-actions' }
            ];

            const fetchEntries = () => {
                loading.value = true;
                request.get('/knowledge/entries', {
                    params: {
                        page: pagination.current, limit: pagination.pageSize,
                        keyword: searchKeyword.value, category_id: currentCategoryId.value,
                        crop: cropKeyword.value
                    }
                }).then((res) => {
                    const data = res.data.data || {};
                    tableData.value = data.list || [];
                    pagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { loading.value = false; });
            };
            // 搜索/筛选变更：重置到第一页再拉取，避免停留在越界页码
            const onFilterChange = () => {
                pagination.current = 1;
                fetchEntries();
            };
            const onPageChange = (info) => {
                pagination.current = info.current;
                pagination.pageSize = info.pageSize;
                fetchEntries();
            };

            // 条目弹窗
            const entryFormVisible = ref(false);
            const entryFormTitle = ref('新增条目');
            const entryEditingId = ref(0);
            const submitting = ref(false);
            const imageTemp = ref('');
            const entryForm = reactive({
                title: '', category_id: null, crop: '', summary: '',
                cause: '', solution: '', images: [], sort_order: 0
            });
            const entryFormRef = ref(null);
            const entryRules = {
                title: [{ required: true, message: '请输入知识标题', type: 'error' }],
                category_id: [{ required: true, message: '请选择所属分类', type: 'error' }]
            };
            // image-upload 上传成功后经 imageTemp 中转，追加到 images 并复位
            watch(imageTemp, (val) => {
                if (val) { entryForm.images.push(val); imageTemp.value = ''; }
            });
            const removeImage = (idx) => { entryForm.images.splice(idx, 1); };

            const resetEntryForm = () => {
                entryForm.title = '';
                entryForm.category_id = currentCategoryId.value || null;
                entryForm.crop = '';
                entryForm.summary = '';
                entryForm.cause = '';
                entryForm.solution = '';
                entryForm.images = [];
                entryForm.sort_order = 0;
                imageTemp.value = '';
            };
            const openEntryCreate = () => {
                entryEditingId.value = 0;
                entryFormTitle.value = '新增条目';
                resetEntryForm();
                entryFormVisible.value = true;
            };
            const openEntryEdit = (row) => {
                entryEditingId.value = row.id;
                entryFormTitle.value = '编辑条目';
                request.get('/knowledge/entries/' + row.id).then((res) => {
                    const d = res.data.data || {};
                    entryForm.title = d.title || '';
                    entryForm.category_id = d.category_id || null;
                    entryForm.crop = d.crop || '';
                    entryForm.summary = d.summary || '';
                    entryForm.cause = d.cause || '';
                    entryForm.solution = d.solution || '';
                    entryForm.images = d.images || [];
                    entryForm.sort_order = d.sort_order || 0;
                    entryFormVisible.value = true;
                }).catch(() => {});
            };
            const submitEntry = () => {
                if (!entryFormRef.value) return;
                entryFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    submitting.value = true;
                    const body = {
                        title: entryForm.title.trim(), category_id: entryForm.category_id,
                        crop: entryForm.crop, summary: entryForm.summary, cause: entryForm.cause,
                        solution: entryForm.solution, images: entryForm.images,
                        sort_order: entryForm.sort_order
                    };
                    const req = entryEditingId.value
                        ? request.put('/knowledge/entries/' + entryEditingId.value, body)
                        : request.post('/knowledge/entries', body);
                    req.then(() => {
                        MessagePlugin.success(entryEditingId.value ? '更新成功' : '创建成功');
                        entryFormVisible.value = false;
                        fetchEntries();
                    }).catch(() => {}).finally(() => { submitting.value = false; });
                });
            };
            const removeEntry = (row) => {
                request.delete('/knowledge/entries/' + row.id).then(() => {
                    MessagePlugin.success('删除成功');
                    fetchEntries();
                }).catch(() => {});
            };

            // ---------------- 批量新增 ----------------
            // 批量新增弹窗（共享分类/作物 + 多行标题，每行一条）
            const batchVisible = ref(false);
            const batchSubmitting = ref(false);
            const batchForm = reactive({ category_id: null, crop: '', titlesText: '' });
            const batchFormRef = ref(null);
            const batchRules = {
                category_id: [{ required: true, message: '请选择所属分类', type: 'error' }],
                titlesText: [{ required: true, message: '请至少输入一个标题', type: 'error' }]
            };
            const openBatch = () => {
                batchForm.category_id = currentCategoryId.value || null;
                batchForm.crop = '';
                batchForm.titlesText = '';
                batchVisible.value = true;
            };
            const submitBatch = () => {
                if (!batchFormRef.value) return;
                batchFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    const titles = batchForm.titlesText.split('\n').map((t) => t.trim()).filter((t) => t);
                    if (!titles.length) return;
                    batchSubmitting.value = true;
                    request.post('/knowledge/entries/batch', {
                        category_id: batchForm.category_id, crop: batchForm.crop,
                        titles: titles
                    }).then((res) => {
                        MessagePlugin.success((res.data && res.data.msg) || ('成功新增 ' + titles.length + ' 条'));
                        batchVisible.value = false;
                        fetchEntries();
                    }).catch(() => {}).finally(() => { batchSubmitting.value = false; });
                });
            };

            onMounted(() => {
                fetchCategories();
                // 先读插件配置 list_page_size 作为列表默认每页条数，再拉取首页
                request.get('/plugin/config/knowledge').then((res) => {
                    const cur = (res.data.data && res.data.data.current) || {};
                    const size = parseInt(cur.list_page_size, 10);
                    if (size > 0) { pagination.pageSize = Math.min(size, 100); }
                }).catch(() => {}).finally(() => { fetchEntries(); });
            });

            return {
                categories, currentCategoryId, categoryOptions, categoryName,
                selectCategory, rootMenu, childMenu, onCatMenu,
                catFormVisible, catFormTitle, catEditingId, catForm, catFormRef, catRules,
                openCatCreate, submitCategory,
                tableData, loading, searchKeyword, cropKeyword,
                pagination, columns, fetchEntries, onFilterChange, onPageChange,
                batchVisible, batchSubmitting, batchForm, batchFormRef, batchRules, openBatch, submitBatch,
                entryFormVisible, entryFormTitle, submitting, entryForm, entryFormRef, entryRules, imageTemp,
                removeImage, openEntryCreate, openEntryEdit, submitEntry, removeEntry
            };
        }
    });
})();
