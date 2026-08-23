/**
 * 文件下载插件 — 后台管理页前端逻辑
 *
 * 由 file_download.html 内联脚本等价外提（降低模板单文件行数）。
 * 采用共享 js/pages 目录承载插件页脚本，与 admin_notifier.js 同范式，
 * 经 /static/js/pages/file_download.js 引入；页面无 Jinja2 变量，无需数据桥接。
 */
(function () {
    const { ref, reactive, computed, onMounted } = Vue;
    const { MessagePlugin } = TDesign;

    HuiYan.createPluginPage({ plugin: 'file_download', page: 'file_download',
        setup() {
            // ------------------------- 文件夹 -------------------------
            const folders = ref([]);
            const uploadPolicy = ref({ max_size_mb: 0, extensions: [] });
            const uploadPolicyReady = ref(false);
            const uploadAccept = computed(() => uploadPolicy.value.extensions.map(ext => '.' + ext).join(','));
            const uploadHint = computed(() => {
                const formats = uploadPolicy.value.extensions.join('/');
                return formats + '，最大 ' + uploadPolicy.value.max_size_mb + 'MB';
            });
            const fetchUploadPolicy = () => request.get('/upload/settings').then((res) => {
                const data = (res.data && res.data.data) || res.data || {};
                const found = (data.policies || []).find(item => item.id === 'file_download.resource');
                if (!found) throw new Error('upload policy unavailable');
                uploadPolicy.value = found;
                uploadPolicyReady.value = true;
            }).catch(() => {
                uploadPolicyReady.value = false;
                MessagePlugin.error('获取资料下载上传规则失败，请刷新重试');
            });
            const currentFolderId = ref(0);
            const totalFileCount = computed(() =>
                folders.value.reduce((sum, f) => sum + (f.file_count || 0), 0));
            const folderOptions = computed(() =>
                folders.value.map((f) => ({ label: f.name, value: f.id })));

            const fetchFolders = () => {
                request.get('/file_download/folders').then((res) => {
                    folders.value = (res.data.data && res.data.data.list) || [];
                }).catch(() => {});
            };

            const selectFolder = (id) => {
                currentFolderId.value = id;
                pagination.current = 1;
                fetchFiles();
            };

            // 文件夹下拉菜单（重命名/设默认/删除）
            const folderMenu = (folder) => {
                const items = [{ content: '重命名', value: 'rename' }];
                if (folder.is_default !== 1) {
                    items.push({ content: '设为默认', value: 'default' });
                    items.push({ content: '删除', value: 'delete', theme: 'error' });
                }
                return items;
            };

            const onFolderMenu = (item, folder) => {
                const action = item.value !== undefined ? item.value : (item.data && item.data.value);
                if (action === 'rename') { openFolderRename(folder); }
                else if (action === 'default') { setDefaultFolder(folder); }
                else if (action === 'delete') { removeFolder(folder); }
            };

            // 新建/重命名文件夹弹窗
            const folderFormVisible = ref(false);
            const folderFormTitle = ref('新建文件夹');
            const folderEditingId = ref(0);
            const folderForm = reactive({ name: '' });
            const folderFormRef = ref(null);
            const folderRules = {
                name: [{ required: true, message: '请输入文件夹名称', type: 'error' }]
            };

            const openFolderCreate = () => {
                folderEditingId.value = 0;
                folderFormTitle.value = '新建文件夹';
                folderForm.name = '';
                folderFormVisible.value = true;
            };

            const openFolderRename = (folder) => {
                folderEditingId.value = folder.id;
                folderFormTitle.value = '重命名文件夹';
                folderForm.name = folder.name;
                folderFormVisible.value = true;
            };

            const submitFolder = () => {
                if (!folderFormRef.value) return;
                folderFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    const req = folderEditingId.value
                        ? request.put('/file_download/folders/' + folderEditingId.value, { name: folderForm.name.trim() })
                        : request.post('/file_download/folders', { name: folderForm.name.trim() });
                    req.then(() => {
                        MessagePlugin.success(folderEditingId.value ? '重命名成功' : '创建成功');
                        folderFormVisible.value = false;
                        fetchFolders();
                    }).catch(() => {});
                });
            };

            const setDefaultFolder = (folder) => {
                request.put('/file_download/folders/' + folder.id + '/default').then(() => {
                    MessagePlugin.success('已设为默认文件夹');
                    fetchFolders();
                }).catch(() => {});
            };

            const removeFolder = (folder) => {
                request.delete('/file_download/folders/' + folder.id).then(() => {
                    MessagePlugin.success('删除成功，夹内文件已移入默认文件夹');
                    if (currentFolderId.value === folder.id) { currentFolderId.value = 0; }
                    fetchFolders();
                    fetchFiles();
                }).catch(() => {});
            };

            // ------------------------- 文件表格 -------------------------
            const tableData = ref([]);
            const loading = ref(false);
            const searchKeyword = ref('');
            const pagination = reactive({ current: 1, pageSize: 10, total: 0 });
            const canUpdate = HuiYan.hasPermission ? HuiYan.hasPermission('file_download:update') : true;
            const columns = [
                { colKey: 'name', title: '名称', minWidth: 200, cell: 'name' },
                { colKey: 'filetype', title: '类型', width: 80 },
                { colKey: 'filesize', title: '大小', width: 100, cell: 'filesize' },
                { colKey: 'visible_range', title: '可见范围', width: 110, cell: 'visible_range' },
                { colKey: 'hidden', title: '显示', width: 90, cell: 'hidden' },
                { colKey: 'download_count', title: '下载次数', width: 100 },
                { colKey: 'create_time', title: '上传时间', width: 175 },
                { colKey: 'actions', title: '操作', width: 180, cell: 'actions' }
            ];

            const fetchFiles = () => {
                loading.value = true;
                request.get('/file_download/files', {
                    params: {
                        page: pagination.current, limit: pagination.pageSize,
                        keyword: searchKeyword.value, folder_id: currentFolderId.value
                    }
                }).then((res) => {
                    const data = res.data.data || {};
                    tableData.value = data.list || [];
                    pagination.total = data.total || 0;
                }).catch(() => {}).finally(() => { loading.value = false; });
            };

            const onPageChange = (pageInfo) => {
                pagination.current = pageInfo.current;
                pagination.pageSize = pageInfo.pageSize;
                fetchFiles();
            };

            // 文件大小人性化显示
            const formatSize = (bytes) => {
                if (!bytes) return '0 B';
                const units = ['B', 'KB', 'MB', 'GB'];
                let value = bytes, i = 0;
                while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
                return value.toFixed(value >= 100 || i === 0 ? 0 : 1) + ' ' + units[i];
            };

            // ------------------------- 产区选项 -------------------------
            const areaOptions = ref([]);
            const fetchAreaOptions = () => {
                request.get('/production-area/options').then((res) => {
                    const data = res.data || {};
                    const rows = data.list || data.data || [];
                    areaOptions.value = rows.map((a) => ({ label: a.name, value: a.id }));
                }).catch(() => {});
            };

            // ------------------------- 上传/编辑 -------------------------
            const fileFormVisible = ref(false);
            const fileFormTitle = ref('上传文件');
            const editingId = ref(0);
            const submitting = ref(false);
            const uploadFiles = ref([]);
            const fileForm = reactive({
                name: '', folder_id: null, visible_range: 'all', area_ids: [], description: '', upload: ''
            });
            const fileFormRef = ref(null);
            const fileRules = {
                upload: [{ validator: () => ({ result: editingId.value || uploadFiles.value.length > 0, message: '请选择要上传的文件' }), type: 'error' }],
                name: [{ validator: () => ({ result: !editingId.value || !!fileForm.name.trim(), message: '请输入显示名称' }), type: 'error' }],
                folder_id: [{ required: true, message: '请选择所属文件夹', type: 'error' }],
                area_ids: [{ validator: () => ({ result: fileForm.visible_range !== 'area' || fileForm.area_ids.length > 0, message: '请选择可见产区' }), type: 'error' }]
            };

            const resetFileForm = () => {
                fileForm.name = '';
                fileForm.folder_id = currentFolderId.value || (folders.value[0] && folders.value[0].id) || null;
                fileForm.visible_range = 'all';
                fileForm.area_ids = [];
                fileForm.description = '';
                uploadFiles.value = [];
            };

            const openUpload = () => {
                if (!uploadPolicyReady.value) { MessagePlugin.error('上传规则尚未加载完成'); return; }
                editingId.value = 0;
                fileFormTitle.value = '上传文件';
                resetFileForm();
                fileFormVisible.value = true;
            };

            const openEdit = (row) => {
                editingId.value = row.id;
                fileFormTitle.value = '编辑文件';
                fileForm.name = row.name;
                fileForm.folder_id = row.folder_id;
                fileForm.visible_range = row.visible_range;
                fileForm.area_ids = row.area_ids || [];
                fileForm.description = row.description || '';
                fileFormVisible.value = true;
            };

            const submitFile = () => {
                if (!fileFormRef.value) return;
                fileFormRef.value.validate().then((valid) => {
                    if (valid !== true) return;
                    if (!editingId.value && !uploadPolicyReady.value) {
                        MessagePlugin.error('上传规则尚未加载完成');
                        return;
                    }
                    submitting.value = true;
                    let req;
                    if (editingId.value) {
                        req = request.put('/file_download/files/' + editingId.value, {
                            name: fileForm.name.trim(),
                            folder_id: fileForm.folder_id,
                            visible_range: fileForm.visible_range,
                            area_ids: fileForm.visible_range === 'area' ? fileForm.area_ids : [],
                            description: fileForm.description
                        });
                    } else {
                        const selected = uploadFiles.value[0].raw;
                        const extension = String(selected.name || '').split('.').pop().toLowerCase();
                        if (!uploadPolicy.value.extensions.includes(extension)
                                || selected.size > uploadPolicy.value.max_size_mb * 1024 * 1024) {
                            MessagePlugin.error('文件格式或大小不符合上传设置');
                            submitting.value = false;
                            return;
                        }
                        // multipart 表单提交：t-upload 收集的原生 File 放入 FormData
                        const fd = new FormData();
                        fd.append('file', uploadFiles.value[0].raw);
                        fd.append('name', fileForm.name.trim());
                        fd.append('folder_id', fileForm.folder_id);
                        fd.append('visible_range', fileForm.visible_range);
                        fd.append('area_ids', fileForm.visible_range === 'area' ? fileForm.area_ids.join(',') : '');
                        fd.append('description', fileForm.description);
                        req = request.post('/file_download/files', fd);
                    }
                    req.then(() => {
                        MessagePlugin.success(editingId.value ? '更新成功' : '上传成功');
                        fileFormVisible.value = false;
                        fetchFiles();
                        fetchFolders();
                    }).catch(() => {}).finally(() => { submitting.value = false; });
                });
            };

            // ------------------------- 显隐/删除/下载 -------------------------
            const toggleHidden = (row) => {
                const newHidden = row.hidden === 0 ? 1 : 0;
                request.put('/file_download/files/' + row.id + '/hidden', { hidden: newHidden }).then(() => {
                    MessagePlugin.success(newHidden === 1 ? '已隐藏' : '已显示');
                    fetchFiles();
                }).catch(() => {});
            };

            const removeRow = (row) => {
                request.delete('/file_download/files/' + row.id).then(() => {
                    MessagePlugin.success('删除成功');
                    fetchFiles();
                    fetchFolders();
                }).catch(() => {});
            };

            // blob 下载：request 自动带 JWT，文件名优先取 Content-Disposition
            const downloadRow = (row) => {
                request.get('/file_download/files/' + row.id + '/download', {
                    responseType: 'blob', skipAutoError: true
                }).then((res) => {
                    const disposition = res.headers['content-disposition'] || '';
                    let filename = row.origin_name || row.name;
                    const match = disposition.match(/filename\*=utf-8''([^;]+)/i);
                    if (match) { filename = decodeURIComponent(match[1]); }
                    const url = URL.createObjectURL(res.data);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = filename;
                    link.click();
                    URL.revokeObjectURL(url);
                }).catch(() => { MessagePlugin.error('下载失败'); });
            };

            onMounted(() => {
                fetchFolders();
                fetchFiles();
                fetchAreaOptions();
                fetchUploadPolicy();
            });

            return {
                folders, currentFolderId, totalFileCount, folderOptions, selectFolder,
                uploadAccept, uploadHint, uploadPolicyReady,
                folderMenu, onFolderMenu,
                folderFormVisible, folderFormTitle, folderForm, folderFormRef, folderRules, openFolderCreate, submitFolder,
                tableData, loading, searchKeyword, pagination, columns, canUpdate,
                fetchFiles, onPageChange, formatSize,
                areaOptions,
                fileFormVisible, fileFormTitle, editingId, submitting, uploadFiles, fileForm, fileFormRef, fileRules,
                openUpload, openEdit, submitFile,
                toggleHidden, removeRow, downloadRow
            };
        }
    });
})();
