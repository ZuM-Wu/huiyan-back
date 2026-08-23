/**
 * 导航管理页面脚本 — 两栏可视编辑器 + 页面类型体系
 * 统一写法：Composition API + ES6 + HuiYan.createPage
 */
(function () {
    const { ref, reactive, computed, onMounted } = Vue;
    const { MessagePlugin, DialogPlugin } = TDesign;

    HuiYan.createPage({
        setup() {
            const navType = ref('admin');          // admin=后台，frontend=前台
            const menuTree = ref([]);              // 树形菜单数据
            const loading = ref(false);
            const registeredList = ref([]);        // 可注册页面（系统 + 插件）

            // 右栏表单状态
            const selectedId = ref(null);          // 当前选中菜单 id
            const isCreating = ref(false);         // 是否处于新建态
            const submitLoading = ref(false);
            const formRef = ref(null);
            const formData = reactive({
                pageType: 'system',               // system/plugin/custom/separator
                registeredKey: '',                // 选中的系统/插件页面 key
                name: '', title: '', path: '', icon: '',
                parent_id: 0, visible: 1,
                plugin: '',                       // 插件标识（插件页面自动填充）
                targetType: ''                    // 外链打开方式：空/_blank/iframe
            });
            const formRules = {
                title: [{ required: true, message: '请输入导航名称', type: 'error' }],
                name: [{ required: true, message: '请输入菜单标识', type: 'error' }]
            };

            // 拖拽状态与处理器由子模块提供（navigation-drag.js），此处仅占位声明，
            // 待 menuTree/navType/fetchData 就绪后在下方统一装配。

            // 图标选择器状态
            const showIconPicker = ref(false);
            const iconSearchText = ref('');

            // 自定义路由子 Tab：plugin=选择插件公开页面，url=自己填写 URL
            const customMode = ref('plugin');

            // 过滤图标列表（搜索时显示全部匹配，无搜索时显示全部图标）
            const filteredIcons = computed(() => {
                const search = (iconSearchText.value || '').toLowerCase();
                if (!search) return TDESIGN_ICONS;
                return TDESIGN_ICONS.filter(n => n.toLowerCase().includes(search));
            });

            const pageTypeOptions = computed(() => {
                // 系统页面/插件页面/自定义路由/分隔符前台后台均可用
                return [
                    { label: '系统页面', value: 'system' },
                    { label: '插件页面', value: 'plugin' },
                    { label: '自定义路由', value: 'custom' },
                    { label: '分隔符', value: 'separator' }
                ];
            });

            // ========== 计算属性：按 nav_type 过滤可用的系统页面 ==========
            const availableSystemPages = computed(() => 
                registeredList.value.filter(p => p.page_type === 'system' && p.source === 'system')
            );
            
            const availablePluginPages = computed(() => 
                registeredList.value.filter(p => p.page_type === 'system' && p.source === 'plugin')
            );

            // 可注册页面下拉：系统页面只显示系统源，插件页面只显示插件源。
            // 输出 t-tree-select 树节点（分组为禁选父节点），并将"已被使用的页面"禁用
            // （同一系统/插件页面最多只能添加一次，重复添加无意义）。
            const registeredOptions = computed(() => {
                const isSystem = formData.pageType === 'system';
                const isPlugin = formData.pageType === 'plugin';
                if (!isSystem && !isPlugin) return [];

                const list = isSystem ? availableSystemPages.value : availablePluginPages.value;
                const used = usedPaths.value;
                const selfPath = currentNodePath.value;

                // 按 group 聚合，保持后端返回顺序
                const groupMap = new Map();
                list.forEach((p) => {
                    const groupName = p.group || (isPlugin ? (p.title || '插件') : '其他');
                    if (!groupMap.has(groupName)) groupMap.set(groupName, []);
                    groupMap.get(groupName).push({
                        label: p.title,
                        value: p.key,
                        // 路径已被其它菜单占用且非当前编辑节点自身 → 禁用
                        disabled: !!p.path && used.has(p.path) && p.path !== selfPath,
                        raw: p
                    });
                });

                const result = [];
                groupMap.forEach((children, group) => {
                    // 分组节点仅作层级展示：activable/checkable 关闭使其不可选中；
                    // 不能用 disabled（Tree 的 disabled 会级联禁用子节点）
                    result.push({ label: group, value: '__group__' + group, activable: false, checkable: false, children });
                });
                return result;
            });

            // 已被使用的页面路径集合（用于"同一页面只能添加一次"的禁用判断）
            const usedPaths = computed(() => {
                const set = new Set();
                const walk = (items) => {
                    items.forEach((node) => {
                        if (node.path) set.add(node.path);
                        if (node.children && node.children.length) walk(node.children);
                    });
                };
                walk(menuTree.value);
                return set;
            });

            // 当前编辑节点自身的路径（编辑态下允许其保留已选中的页面）
            const currentNodePath = computed(() => {
                if (!selectedId.value) return '';
                const node = findNodeById(selectedId.value);
                return node ? (node.path || '') : '';
            });

            // 自定义路由 > "插件公开页面" 子 Tab 的候选项：按插件分组的树节点，value 为页面路由。
            // 与"插件页面"类型不同，此处仅作为快捷入口，可重复添加、不做去重禁用。
            const pluginPageOptions = computed(() => {
                const groupMap = new Map();
                availablePluginPages.value.forEach((p) => {
                    const groupName = p.group || (p.title || '插件');
                    if (!groupMap.has(groupName)) groupMap.set(groupName, []);
                    groupMap.get(groupName).push({ label: p.title, value: p.path, raw: p });
                });
                const result = [];
                groupMap.forEach((children, group) => {
                    result.push({ label: group, value: '__group__' + group, activable: false, checkable: false, children });
                });
                return result;
            });

            // 分组父节点兼容判断：分组节点值以 __group__ 前缀标记，选中无意义需重置
            const isGroupValue = (val) => typeof val === 'string' && val.indexOf('__group__') === 0;

            // 父级菜单下拉：顶级 + 各顶级分组（排除自身）
            const parentOptions = computed(() => {
                const opts = [{ label: '顶级菜单', value: 0 }];
                menuTree.value.forEach((item) => {
                    if (item.id !== selectedId.value) {
                        opts.push({ label: item.title, value: item.id });
                    }
                });
                return opts;
            });

            // 将树平铺为 [{node, depth}]，保持排序顺序供左栏渲染
            const flatMenus = computed(() => {
                const result = [];
                const walk = (items, depth) => {
                    items.forEach((node) => {
                        result.push({ node, depth });
                        if (node.children && node.children.length) walk(node.children, depth + 1);
                    });
                };
                walk(menuTree.value, 0);
                return result;
            });

            // ========== 数据加载 ==========
            const fetchData = () => {
                loading.value = true;
                request.get('/menu/tree', { params: { nav_type: navType.value } }).then((res) => {
                    const data = res.data.data || res.data || {};
                    menuTree.value = data.list || [];
                }).catch(() => {}).finally(() => { loading.value = false; });
            };

            const fetchRegistered = () => {
                // 按当前 nav_type 拉取可注册页面：后台=系统页+插件后台页，前台=仅插件前台页
                // /menu/registered 返回统一信封 {status,msg,data:{system,plugin}}，需取 res.data.data 解包
                request.get('/menu/registered', { params: { nav_type: navType.value } }).then((res) => {
                    const d = res.data.data || {};
                    registeredList.value = [].concat(d.system || [], d.plugin || []);
                }).catch(() => {});
            };

            // ========== 拖拽子模块装配 ==========
            // 拖拽逻辑拆至 navigation-drag.js；传入菜单树/导航类型/刷新回调，
            // 回传拖拽状态与处理器。findNodeById 供 currentNodePath 复用。
            const drag = window.HuiYanNavDrag.create({ menuTree, navType, fetchData });
            const { dragOverId, dragOverType, findNodeById,
                onDragStart, onDragOver, onDragLeave, onDragEnd, onDrop } = drag;

            const onTabChange = () => {
                resetSelection();
                fetchData();
                fetchRegistered();
            };

            // ========== 右栏表单 ==========
            const resetSelection = () => {
                selectedId.value = null;
                isCreating.value = false;
                dragOverId.value = null;
            };

            const fillForm = (data) => {
                Object.assign(formData, {
                    pageType: 'system', registeredKey: '',
                    name: '', title: '', path: '', icon: '',
                    parent_id: 0, visible: 1, plugin: '', targetType: ''
                }, data);
            };

            const selectNode = (node) => {
                isCreating.value = false;
                selectedId.value = node.id;
                // 根据已有数据推断 pageType 和 targetType
                const nodePath = node.path || '';
                let pageType = node.page_type || 'system';
                // DB 存储 'url'，表单内部用 'custom'
                if (pageType === 'url') pageType = 'custom';
                // 外部链接且 page_type 还是 system，自动修正为 custom
                if (isExternalLink(nodePath) && pageType === 'system') {
                    pageType = 'custom';
                }
                fillForm({
                    name: node.name, title: node.title, path: nodePath, icon: node.icon || '',
                    parent_id: node.parent_id || 0,
                    visible: node.visible === 0 ? 0 : 1,
                    pageType: pageType,
                    targetType: node.target_type || ''
                });
                // 区分系统页面与插件页面（两者 page_type 均存为 'system'）：
                // 若路径命中插件页而非系统预设页，则视为插件页面
                if (formData.pageType === 'system') {
                    const isPluginPage = availablePluginPages.value.some((p) => p.path === nodePath);
                    const isSystemPage = availableSystemPages.value.some((p) => p.path === nodePath);
                    if (isPluginPage && !isSystemPage) formData.pageType = 'plugin';
                }
                // 自定义路由：根据路径是否命中插件页面推断子 Tab
                if (formData.pageType === 'custom') {
                    const isPluginPage = availablePluginPages.value.some((p) => p.path === nodePath);
                    customMode.value = isPluginPage ? 'plugin' : 'url';
                }
            };

            const openCreate = () => {
                selectedId.value = null;
                isCreating.value = true;
                fillForm({});
                formData.pageType = 'system';
            };

            const onPageTypeChange = () => {
                if (formData.pageType === 'custom') {
                    formData.registeredKey = '';
                    formData.path = '';
                    // 自定义路由默认进入"插件公开页面"子 Tab
                    customMode.value = 'plugin';
                    formData.targetType = '';
                } else if (formData.pageType === 'separator') {
                    formData.registeredKey = '';
                    formData.path = '';
                    formData.name = '';
                    formData.targetType = '';
                } else {
                    // 系统页面不需要 targetType
                    formData.targetType = '';
                }
            };

            // 选择系统/插件页面后回填名称/路由/图标/标识（分组父节点被选中时重置）
            const onRegisteredChange = (val) => {
                if (isGroupValue(val)) {
                    formData.registeredKey = '';
                    return;
                }
                const found = registeredList.value.find((p) => p.key === val);
                if (found) {
                    formData.title = found.title;
                    formData.path = found.path;
                    formData.icon = found.icon || '';
                    if (!formData.name) formData.name = found.key;
                    // 插件页面自动填充 plugin 标识，确保卸载时可清理
                    if (found.source === 'plugin' && found.plugin) {
                        formData.plugin = found.plugin;
                    }
                }
            };

            // 切换自定义路由子 Tab：清空已填内容，避免两种来源相互干扰
            const onCustomModeChange = () => {
                formData.path = '';
                // 插件公开页面在内容区正常打开（target 置空）；自定义 URL 默认新标签页
                formData.targetType = customMode.value === 'url' ? '_blank' : '';
            };

            // 选择插件公开页面后，回填标题/图标/标识（仅在未填写时），路径即选中值；分组父节点被选中时重置
            const onPluginPageChange = (val) => {
                if (isGroupValue(val)) {
                    formData.path = '';
                    return;
                }
                const found = availablePluginPages.value.find((p) => p.path === val);
                if (found) {
                    if (!formData.title) formData.title = found.title;
                    if (!formData.icon) formData.icon = found.icon || '';
                    if (!formData.name) formData.name = found.key;
                }
                // 插件公开页面为内部路由，默认在内容区打开
                formData.targetType = '';
            };

            // 判断是否为外部链接（http/https）
            const isExternalLink = (path) => {
                return path && (path.startsWith('http://') || path.startsWith('https://'));
            };

            const buildPayload = () => ({
                name: formData.name, title: formData.title, path: formData.path, icon: formData.icon,
                parent_id: formData.parent_id, visible: formData.visible, nav_type: navType.value,
                page_type: formData.pageType === 'separator' ? 'separator' : (formData.pageType === 'custom' ? 'url' : 'system'),
                target_type: formData.pageType === 'custom' ? (formData.targetType || '') : '',
                plugin: formData.plugin || ''
            });

            const doSubmit = () => {
                if (!formRef.value) return;
                // TDesign 表单校验：未通过时错误已内联显示，直接中断
                formRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    submitLoading.value = true;
                    const promise = isCreating.value
                        ? request.post('/menu/create', buildPayload())
                        : request.put('/menu/' + selectedId.value, buildPayload());
                    promise.then(() => {
                        MessagePlugin.success(isCreating.value ? '创建成功' : '已应用');
                        localStorage.removeItem('admin_menus');  // 清缓存，侧边栏下次刷新拉最新
                        resetSelection();
                        fetchData();
                    }).catch(() => { MessagePlugin.error('操作失败'); })
                        .finally(() => { submitLoading.value = false; });
                });
            };

            // 快捷切换显示/隐藏（双击节点或点击“隐藏”标签）
            const quickToggleVisible = (node) => {
                const newVisible = node.visible === 0 ? 1 : 0;
                request.put('/menu/' + node.id, {
                    name: node.name, title: node.title, path: node.path || '',
                    icon: node.icon || '', visible: newVisible, nav_type: navType.value,
                    page_type: node.page_type || 'system'
                }).then(() => {
                    node.visible = newVisible;
                    MessagePlugin.success(newVisible ? '已显示' : '已隐藏');
                    localStorage.removeItem('admin_menus');
                }).catch(() => { MessagePlugin.error('切换失败'); });
            };

            const confirmDelete = () => {
                const dlg = DialogPlugin.confirm({
                    header: '确认删除',
                    body: '删除菜单 [' + formData.title + '] 将同时删除其子菜单，确定继续？',
                    confirmBtn: { content: '确认', theme: 'danger' },
                    onConfirm: () => {
                        dlg.setConfirmLoading(true);
                        request.delete('/menu/' + selectedId.value).then(() => {
                            MessagePlugin.success('已删除');
                            localStorage.removeItem('admin_menus');
                            resetSelection();
                            fetchData();
                        }).catch(() => { 
                            MessagePlugin.error('删除失败'); 
                        }).finally(() => {
                            dlg.setConfirmLoading(false);
                            dlg.destroy();
                        });
                    },
                    onClose: () => { dlg.destroy(); }
                });
            };

            onMounted(() => { fetchData(); fetchRegistered(); });
            return {
                navType, loading, flatMenus, selectedId, isCreating,
                submitLoading, formRef, formData, formRules,
                pageTypeOptions, registeredOptions, parentOptions,
                customMode, pluginPageOptions,
                dragOverId, dragOverType,
                showIconPicker, iconSearchText, filteredIcons,
                onTabChange, selectNode, openCreate, onPageTypeChange, onRegisteredChange,
                onCustomModeChange, onPluginPageChange,
                doSubmit, confirmDelete, quickToggleVisible,
                onDragStart, onDragOver, onDragLeave, onDragEnd, onDrop
            };
        }
    });
})();
