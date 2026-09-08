/**
 * 变量提示共享组件。
 *
 * 统一变量说明的触发按钮、弹层宽度与表格列宽，避免页面内各自实现导致内容溢出。
 */
(function (window) {
    'use strict';

    if (!window.Vue) { return; }

    var VariableHint = {
        name: 'VariableHint',
        props: {
            title: { type: String, default: '可用变量' },
            items: { type: Array, default: function () { return []; } },
        },
        setup: function () {
            return {
                columns: [
                    { colKey: 'var', title: '变量', width: 128 },
                    { colKey: 'desc', title: '说明', ellipsis: true },
                ],
            };
        },
        template: `
            <t-popup trigger="click" placement="top-left" :show-arrow="true">
                <t-button variant="text" theme="default">
                    <template #icon><t-icon name="help-circle"></t-icon></template>
                    变量提示
                </t-button>
                <template #content>
                    <section class="variable-hint-popup">
                        <h4 class="variable-hint-popup__title">{{ title }}</h4>
                        <t-table :data="items" :columns="columns" row-key="var" :pagination="null" table-layout="fixed" hover></t-table>
                    </section>
                </template>
            </t-popup>
        `,
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['variable-hint'] = VariableHint;
})(window);
