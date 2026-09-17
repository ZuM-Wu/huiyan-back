/**
 * 对象存储配置与层级文件管理组件。
 *
 * 组件只依赖公共 request 客户端和 TDesign，插件名称、能力和接口均由
 * 后端存储列表返回，不在前端硬编码具体存储厂商。
 */
(function (window) {
    'use strict';

    // 文件管理器默认每页 10 条，后续只需调整此常量即可改变单页高度。
    var FILE_PAGE_SIZE = 10;
    var IMAGE_EXTENSIONS = /\.(?:avif|bmp|gif|jpe?g|png|svg|webp)$/i;
    var ObjectStorageManager = {
        name: 'ObjectStorageManager',
        setup: function () {
            var configVisible = Vue.ref(false);
            var fileVisible = Vue.ref(false);
            var selected = Vue.ref(null);
            var schema = Vue.ref([]);
            var configForm = Vue.reactive({});
            var configLoading = Vue.ref(false);
            var saving = Vue.ref(false);
            var testing = Vue.ref(false);
            var fileLoading = Vue.ref(false);
            var uploading = Vue.ref(false);
            var currentPath = Vue.ref('');
            var marker = Vue.ref('');
            var files = Vue.ref([]);
            var nextMarker = Vue.ref('');
            var currentPage = Vue.ref(1);
            var pageMarkers = Vue.ref(['']);
            var fileInput = Vue.ref(null);
            var expandedFolders = Vue.ref(['__root__']);
            var previewUrls = Vue.reactive({});
            var previewLoading = Vue.reactive({});
            var folderTree = Vue.ref([
                { value: '__root__', label: '存储根目录', children: [] }
            ]);
            var MessagePlugin = TDesign.MessagePlugin;

            var unwrap = function (res) { return (res.data && res.data.data) || {}; };
            var errorText = function (error, fallback) {
                var data = error && error.response && error.response.data;
                var detail = data && (data.detail || data.msg);
                if (detail && typeof detail === 'object') detail = detail.message || detail.detail;
                return detail || (error && error.message) || fallback;
            };
            var showError = function (error, fallback) {
                MessagePlugin.error(errorText(error, fallback));
            };
            var folderName = function (path) {
                var parts = String(path || '').replace(/\/+$/, '').split('/');
                return parts[parts.length - 1] || '存储根目录';
            };
            var fileName = function (key) {
                var parts = String(key || '').split('/');
                return parts[parts.length - 1] || key || '--';
            };
            var formatSize = function (value) {
                var size = Number(value) || 0;
                if (size < 1024) return size + ' B';
                if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
                if (size < 1024 * 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
                return (size / 1024 / 1024 / 1024).toFixed(1) + ' GB';
            };
            var formatTime = function (value) {
                var raw = Number(value);
                if (!raw) return '--';
                var date = new Date(raw > 100000000000000 ? raw / 10000 : raw);
                return isNaN(date.getTime()) ? '--' : date.toLocaleString();
            };
            var isImage = function (item) {
                var mime = String(item && (item.mimeType || item.mime_type || item.contentType || '') || '').toLowerCase();
                return mime.indexOf('image/') === 0 || IMAGE_EXTENSIONS.test(String(item && item.key || ''));
            };
            var clearPreviewState = function () {
                Object.keys(previewUrls).forEach(function (key) { delete previewUrls[key]; });
                Object.keys(previewLoading).forEach(function (key) { delete previewLoading[key]; });
            };
            var pathParts = Vue.computed(function () {
                var result = [], running = '';
                String(currentPath.value || '').split('/').filter(Boolean).forEach(function (part) {
                    running = running ? running + '/' + part : part;
                    result.push({ label: part, path: running });
                });
                return result;
            });
            var findFolder = function (nodes, path) {
                for (var index = 0; index < nodes.length; index += 1) {
                    var nodePath = nodes[index].value === '__root__' ? '' : nodes[index].value;
                    if (nodePath === path) return nodes[index];
                    var found = findFolder(nodes[index].children || [], path);
                    if (found) return found;
                }
                return null;
            };
            var mergeFolders = function (prefixes) {
                var parent = findFolder(folderTree.value, currentPath.value);
                if (!parent) return;
                var existing = {};
                (parent.children || []).forEach(function (node) { existing[node.value] = node; });
                (prefixes || []).forEach(function (value) {
                    var path = String(value || '').replace(/\/+$/, '');
                    if (path && path !== currentPath.value && !existing[path]) {
                        existing[path] = { value: path, label: folderName(path), children: [] };
                    }
                });
                parent.children = Object.keys(existing).sort().map(function (path) { return existing[path]; });
                parent.isLeaf = parent.children.length === 0;
                folderTree.value = folderTree.value.slice();
            };

            var openConfig = function (row) {
                selected.value = row;
                configVisible.value = true;
                configLoading.value = true;
                request.get('/plugin/config/' + encodeURIComponent(row.name), { skipAutoError: true })
                    .then(function (res) {
                        var data = unwrap(res);
                        schema.value = data.schema || [];
                        Object.keys(configForm).forEach(function (key) { delete configForm[key]; });
                        (schema.value || []).forEach(function (item) {
                            var value = (data.current || {})[item.key] ?? item.default ?? '';
                            configForm[item.key] = item.type === 'password' ? ''
                                : (item.type === 'switch' && value !== '' ? String(value) : value);
                        });
                    }).catch(function (error) {
                        configVisible.value = false;
                        showError(error, '加载存储配置失败');
                    }).finally(function () { configLoading.value = false; });
            };
            var saveConfig = function () {
                saving.value = true;
                request.put('/plugin/config/' + encodeURIComponent(selected.value.name), { config: Object.assign({}, configForm) }, { skipAutoError: true })
                    .then(function () { MessagePlugin.success('存储配置已保存，可继续测试连接'); })
                    .catch(function (error) { showError(error, '存储配置保存失败'); })
                    .finally(function () { saving.value = false; });
            };
            var testConnection = function () {
                if (!selected.value) return;
                testing.value = true;
                request.post('/plugins/' + encodeURIComponent(selected.value.name) + '/test', null, { skipAutoError: true })
                    .then(function (res) { MessagePlugin.success((res.data && res.data.msg) || '连接成功'); })
                    .catch(function (error) { showError(error, '连接测试失败'); })
                    .finally(function () { testing.value = false; });
            };
            var loadFiles = function (reset) {
                if (!selected.value) return;
                if (reset) {
                    marker.value = '';
                    currentPage.value = 1;
                    pageMarkers.value = [''];
                }
                clearPreviewState();
                fileLoading.value = true;
                request.get('/plugins/' + encodeURIComponent(selected.value.name) + '/files', {
                    params: {
                        prefix: currentPath.value,
                        marker: marker.value,
                        limit: FILE_PAGE_SIZE,
                        include_folders: Boolean(reset)
                    }, skipAutoError: true
                }).then(function (res) {
                    var data = unwrap(res);
                    var rows = data.items || [];
                    // 七牛 marker 分页按页替换，避免连续点击下一页把弹窗无限撑高。
                    files.value = rows;
                    mergeFolders(data.common_prefixes || []);
                    nextMarker.value = data.marker || '';
                    rows.filter(isImage).forEach(loadPreviewUrl);
                }).catch(function (error) {
                    showError(error, '文件列表加载失败');
                }).finally(function () { fileLoading.value = false; });
            };
            var openFolder = function (path) {
                currentPath.value = path || '';
                files.value = [];
                nextMarker.value = '';
                loadFiles(true);
            };
            var selectFolder = function (event) {
                var node = event && event.node ? event.node : event;
                // TDesign 不同小版本的 click 事件可能返回 TreeNode 或其原始 data，
                // 两种结构都优先读取 value，确保节点值始终是完整对象前缀。
                var data = node && node.data ? node.data : node;
                var path = data && data.value !== undefined ? data.value : (data && data.path);
                openFolder(path === '__root__' ? '' : path);
            };
            var openFiles = function (row) {
                selected.value = row;
                currentPath.value = '';
                marker.value = '';
                nextMarker.value = '';
                currentPage.value = 1;
                pageMarkers.value = [''];
                expandedFolders.value = ['__root__'];
                clearPreviewState();
                files.value = [];
                folderTree.value = [{ value: '__root__', label: '存储根目录', children: [] }];
                fileVisible.value = true;
                loadFiles(true);
            };
            var nextPage = function () {
                if (fileLoading.value || !nextMarker.value) return;
                pageMarkers.value[currentPage.value] = nextMarker.value;
                marker.value = nextMarker.value;
                currentPage.value += 1;
                loadFiles(false);
            };
            var previousPage = function () {
                if (fileLoading.value || currentPage.value <= 1) return;
                currentPage.value -= 1;
                marker.value = pageMarkers.value[currentPage.value - 1] || '';
                loadFiles(false);
            };
            var pickFile = function () { if (fileInput.value) fileInput.value.click(); };
            var upload = function (event) {
                var file = event.target.files && event.target.files[0];
                if (!file || !selected.value) return;
                var body = new FormData();
                body.append('file', file);
                body.append('prefix', currentPath.value || '');
                uploading.value = true;
                request.post('/plugins/' + encodeURIComponent(selected.value.name) + '/files', body, { skipAutoError: true })
                    .then(function () { MessagePlugin.success('文件上传成功'); loadFiles(true); })
                    .catch(function (error) { showError(error, '文件上传失败'); })
                    .finally(function () { uploading.value = false; event.target.value = ''; });
            };
            var accessUrl = function (item, action) {
                return request.post('/plugins/' + encodeURIComponent(selected.value.name) + '/files/access-url', {
                    key: item.key, action: action || 'preview', expires: 3600
                }, { skipAutoError: true }).then(function (res) { return unwrap(res).url; });
            };
            var loadPreviewUrl = function (item) {
                if (!isImage(item) || previewUrls[item.key] || previewLoading[item.key]) return;
                previewLoading[item.key] = true;
                accessUrl(item, 'preview').then(function (url) {
                    if (url) previewUrls[item.key] = url;
                }).catch(function () {
                    // 缩略图失败时保留文件占位，不为每个失败对象弹出提示打断浏览。
                }).finally(function () { previewLoading[item.key] = false; });
            };
            var download = function (item) {
                accessUrl(item, 'download').then(function (url) {
                    var link = document.createElement('a');
                    link.href = url;
                    link.download = fileName(item.key);
                    link.target = '_blank';
                    link.rel = 'noopener';
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                }).catch(function (error) { showError(error, '生成下载地址失败'); });
            };
            var copyUrl = function (item) {
                accessUrl(item, 'preview').then(function (url) {
                    return navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject(new Error('clipboard'));
                }).then(function () { MessagePlugin.success('访问地址已复制'); })
                    .catch(function (error) { showError(error, '复制失败，请手动打开预览地址'); });
            };
            var remove = function (item) {
                TDesign.DialogPlugin.confirm({
                    header: '确认删除', body: '删除后无法恢复，是否继续？',
                    onConfirm: function () {
                        request.delete('/plugins/' + encodeURIComponent(selected.value.name) + '/files', {
                            data: { key: item.key }, skipAutoError: true
                        })
                            .then(function () { MessagePlugin.success('文件已删除'); loadFiles(true); })
                            .catch(function (error) { showError(error, '文件删除失败'); });
                    }
                });
            };
            return {
                configVisible, fileVisible, selected, schema, configForm, configLoading, saving, testing,
                fileLoading, uploading, currentPath, pathParts, files, folderTree, nextMarker, fileInput,
                currentPage, expandedFolders, previewUrls, previewLoading,
                openConfig, saveConfig, testConnection, openFiles, openFolder, selectFolder, loadFiles,
                nextPage, previousPage, pickFile, upload, download, copyUrl, remove, loadPreviewUrl,
                isImage, fileName, formatSize, formatTime
            };
        },
        template: `
            <t-dialog v-model:visible="configVisible" :header="(selected ? selected.title : '') + '配置'" width="720px" :footer="false">
                <t-skeleton v-if="configLoading" :row-col="[4, 4, 4]" animation="gradient"></t-skeleton>
                <t-form v-else :data="configForm" @submit="saveConfig">
                    <t-form-item v-for="item in schema" :key="item.key" :label="item.label || item.key">
                        <t-input v-if="item.type !== 'select' && item.type !== 'switch'" v-model="configForm[item.key]"
                            :type="item.type === 'password' ? 'password' : 'text'" :placeholder="item.type === 'password' ? '留空保留已保存凭据' : item.placeholder"></t-input>
                        <t-select v-else-if="item.type === 'select'" v-model="configForm[item.key]" :options="item.options || []"></t-select>
                        <t-radio-group v-else v-model="configForm[item.key]">
                            <t-radio v-for="option in (item.options || [{label: '启用', value: '1'}, {label: '停用', value: '0'}])"
                                :key="option.value" :value="option.value">[[ option.label ]]</t-radio>
                        </t-radio-group>
                    </t-form-item>
                    <div style="display:flex;gap:var(--app-spacing-sm);justify-content:flex-end;margin-top:var(--app-spacing-md);">
                        <t-button theme="primary" type="submit" :loading="saving">保存配置</t-button>
                        <t-button theme="default" :loading="testing" @click="testConnection">测试连接</t-button>
                        <t-button theme="default" @click="configVisible = false">取消</t-button>
                    </div>
                </t-form>
            </t-dialog>
            <t-dialog v-model:visible="fileVisible" class="object-storage-manager__dialog" :header="(selected ? selected.title : '') + '文件管理'" width="min(1200px, calc(100vw - 48px))" :footer="false">
                <div class="card-header-toolbar mb16">
                    <div class="card-header-toolbar-left object-storage-manager__pathbar">
                        <t-breadcrumb>
                            <t-breadcrumb-item><t-button theme="default" variant="text" @click="openFolder('')">存储根目录</t-button></t-breadcrumb-item>
                            <t-breadcrumb-item v-for="part in pathParts" :key="part.path">
                                <t-button theme="default" variant="text" @click="openFolder(part.path)">[[ part.label ]]</t-button>
                            </t-breadcrumb-item>
                        </t-breadcrumb>
                    </div>
                    <div class="card-header-toolbar-right">
                        <t-space :size="8">
                            <t-button theme="primary" :loading="uploading" @click="pickFile">上传文件</t-button>
                            <t-button theme="default" :loading="fileLoading" @click="loadFiles(true)">刷新</t-button>
                        </t-space>
                        <input ref="fileInput" type="file" style="display:none" @change="upload">
                    </div>
                </div>
                <div class="object-storage-manager__layout">
                    <div class="object-storage-manager__tree">
                        <t-tree v-model:expanded="expandedFolders" :default-expanded="['__root__']" :data="folderTree" :keys="{ value: 'value', label: 'label', children: 'children' }"
                            :line="true" :expand-on-click-node="true" @click="selectFolder"></t-tree>
                    </div>
                    <div class="object-storage-manager__files">
                        <t-loading :loading="fileLoading">
                            <t-empty v-if="!files.length" description="当前目录暂无文件"></t-empty>
                            <t-list v-else :split="true" class="object-storage-manager__file-list">
                                <t-list-item v-for="row in files" :key="row.key">
                                    <div class="object-storage-manager__file-row">
                                        <div class="object-storage-manager__file-thumb">
                                            <t-image-viewer v-if="isImage(row) && previewUrls[row.key]" :images="[previewUrls[row.key]]">
                                                <template #trigger="{ open }">
                                                    <button type="button" class="object-storage-manager__image-trigger"
                                                        :aria-label="'预览图片：' + fileName(row.key)" @click="open">
                                                        <img class="object-storage-manager__image-thumb" :src="previewUrls[row.key]" :alt="fileName(row.key)">
                                                    </button>
                                                </template>
                                            </t-image-viewer>
                                            <div v-else class="object-storage-manager__file-placeholder">
                                                <t-icon :name="isImage(row) ? 'image' : 'file'" />
                                            </div>
                                        </div>
                                        <div class="object-storage-manager__file-info">
                                            <div class="object-storage-manager__file-name" :title="row.key">[[ fileName(row.key) ]]</div>
                                            <div class="object-storage-manager__file-meta">[[ formatSize(row.fsize) ]] · [[ formatTime(row.putTime) ]]</div>
                                        </div>
                                        <t-space :size="8">
                                            <t-button variant="text" @click="download(row)">下载</t-button>
                                            <t-button variant="text" @click="copyUrl(row)">复制地址</t-button>
                                            <t-button theme="danger" variant="text" @click="remove(row)">删除</t-button>
                                        </t-space>
                                    </div>
                                </t-list-item>
                            </t-list>
                        </t-loading>
                    </div>
                </div>
                <div class="card-header-toolbar mt16">
                    <div class="card-header-toolbar-left">
                        <span class="object-storage-manager__page-label">第 [[ currentPage ]] 页</span>
                    </div>
                    <div class="card-header-toolbar-right">
                        <t-space :size="8">
                            <t-button theme="default" :disabled="currentPage <= 1 || fileLoading" @click="previousPage">上一页</t-button>
                            <t-button theme="default" :disabled="!nextMarker || fileLoading" @click="nextPage">下一页</t-button>
                        </t-space>
                    </div>
                </div>
            </t-dialog>
        `
    };
    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['object-storage-manager'] = ObjectStorageManager;
})(window);
