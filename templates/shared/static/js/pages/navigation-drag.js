/**
 * 导航管理页 — 拖拽排序子模块
 * 从 navigation.js 拆出，降低单文件行数（拖拽逻辑自成一体）。
 * 以工厂函数形式暴露：接收主页面上下文（菜单树 ref、导航类型 ref、数据刷新回调），
 * 返回拖拽状态与事件处理器。须在 navigation.js 之前加载。
 * 依赖全局：Vue、TDesign、request（均由前置脚本提供）。
 */
(function () {
    'use strict';
    const { ref } = Vue;
    const { MessagePlugin } = TDesign;

    window.HuiYanNavDrag = {
        /**
         * 创建拖拽模块
         * @param {Object} ctx - { menuTree: Ref, navType: Ref, fetchData: Function }
         * @returns 拖拽状态 ref 与处理器集合
         */
        create: function (ctx) {
            const menuTree = ctx.menuTree;
            const navType = ctx.navType;
            const fetchData = ctx.fetchData;

            // 拖拽状态
            const draggingNode = ref(null);
            const dragOverId = ref(null);
            const dragOverType = ref(null);  // 'sibling' | 'child'

            // ========== 树操作辅助 ==========
            const findNodeById = (id, tree = menuTree.value) => {
                for (const node of tree) {
                    if (node.id === id) return node;
                    if (node.children) {
                        const found = findNodeById(id, node.children);
                        if (found) return found;
                    }
                }
                return null;
            };

            const removeNodeFromTree = (id, tree = menuTree.value) => {
                for (let i = 0; i < tree.length; i++) {
                    if (tree[i].id === id) {
                        tree.splice(i, 1);
                        return true;
                    }
                    if (tree[i].children && removeNodeFromTree(id, tree[i].children)) {
                        return true;
                    }
                }
                return false;
            };

            const insertNodeAfter = (targetId, newNode, tree = menuTree.value) => {
                for (let i = 0; i < tree.length; i++) {
                    if (tree[i].id === targetId) {
                        tree.splice(i + 1, 0, newNode);
                        return true;
                    }
                    if (tree[i].children && insertNodeAfter(targetId, newNode, tree[i].children)) {
                        return true;
                    }
                }
                return false;
            };

            const onDragStart = (row, ev) => {
                draggingNode.value = row.node;
                ev.dataTransfer.effectAllowed = 'move';
            };

            const onDragOver = (row, ev) => {
                if (!draggingNode.value || draggingNode.value.id === row.node.id) return;
                ev.preventDefault();

                const rect = ev.currentTarget.getBoundingClientRect();
                const offsetY = ev.clientY - rect.top;
                const offsetX = ev.clientX - rect.left;
                const height = rect.height;
                const width = rect.width;
                const targetIsRoot = !row.node.parent_id;

                // 只有根级节点 + 鼠标在底部 15% 且偏右 → 变为子节点（防止误触）
                if (targetIsRoot && offsetY > height * 0.85 && offsetX > width * 0.3) {
                    dragOverId.value = row.node.id;
                    dragOverType.value = 'child';
                } else {
                    dragOverId.value = row.node.id;
                    dragOverType.value = 'sibling';
                }
            };

            const onDragLeave = (row) => {
                if (dragOverId.value === row.node.id) {
                    dragOverId.value = null;
                    dragOverType.value = null;
                }
            };

            const onDragEnd = () => {
                draggingNode.value = null;
                dragOverId.value = null;
                dragOverType.value = null;
            };

            const onDrop = (row) => {
                const dragged = draggingNode.value;
                const target = row.node;
                const dropType = dragOverType.value;
                onDragEnd();

                if (!dragged || dragged.id === target.id) return;

                // 情况 1：拖入子级（变为 target 的子节点）
                if (dropType === 'child') {
                    // 防止将父级拖入自己内部
                    if (isDescendant(target.id, dragged)) {
                        MessagePlugin.warning('不能将父级拖入子级内部');
                        return;
                    }
                    removeNodeFromTree(dragged.id);
                    dragged.parent_id = target.id;
                    if (!target.children) target.children = [];
                    target.children.push(dragged);

                    // 持久化
                    request.put('/menu/' + dragged.id, {
                        parent_id: target.id,
                        name: dragged.name, title: dragged.title,
                        path: dragged.path || '', icon: dragged.icon || '',
                        visible: dragged.visible ? 1 : 0, nav_type: navType.value,
                        page_type: dragged.page_type || 'system'
                    }).then(() => {
                        MessagePlugin.success('已移动到 ' + target.title + ' 下');
                        localStorage.removeItem('admin_menus');
                        fetchData();
                    }).catch(() => {
                        MessagePlugin.error('移动失败');
                        fetchData();
                    });
                    return;
                }

                // 情况 2：跨层级兄弟模式 → 拒绝（防止父级意外变成子级的同级）
                if (dragged.parent_id !== target.parent_id) {
                    MessagePlugin.warning('无法跨层级排序，请拖入为子级或使用编辑面板修改父级');
                    return;
                }

                // 情况 3：同父级内部排序
                const siblings = findSiblings(dragged.parent_id);
                if (!siblings) return;
                const from = siblings.findIndex((n) => n.id === dragged.id);
                const to = siblings.findIndex((n) => n.id === target.id);
                if (from < 0 || to < 0) return;
                siblings.splice(from, 1);
                const newTo = siblings.findIndex((n) => n.id === target.id);
                siblings.splice(newTo + (from < to ? 1 : 0), 0, dragged);

                // 重算 sort_order 并持久化
                const items = siblings.map((n, i) => {
                    n.sort_order = i;
                    return { id: n.id, sort_order: i, parent_id: n.parent_id || 0 };
                });
                request.put('/menu/reorder', { items }).then(() => {
                    MessagePlugin.success('排序已更新');
                    localStorage.removeItem('admin_menus');
                    fetchData();
                }).catch(() => { MessagePlugin.error('排序更新失败'); fetchData(); });
            };

            // 检查 targetId 是否是 parent 的后代
            const isDescendant = (targetId, parent) => {
                if (!parent.children) return false;
                for (const child of parent.children) {
                    if (child.id === targetId) return true;
                    if (isDescendant(targetId, child)) return true;
                }
                return false;
            };

            const findSiblings = (parentId) => {
                if (!parentId) return menuTree.value;
                const parent = findNodeById(parentId);
                return parent && parent.children ? parent.children : null;
            };

            return {
                dragOverId, dragOverType,
                findNodeById,
                onDragStart, onDragOver, onDragLeave, onDragEnd, onDrop,
            };
        }
    };
})();
