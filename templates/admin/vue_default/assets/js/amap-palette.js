/**
 * 高德地图组件共享调色板 amap-palette — amap-board / amap-thumb / amap-picker 三组件共用
 *
 * 规范豁免声明：高德 Web JS API 的多边形/文本标注在 canvas 上绘制，
 * 无法引用 theme.css 的 CSS 变量，只能直写色值。为避免三处组件散落重复
 * 硬编码，统一收拢到本文件，组件内一律引用 window.AmapPalette，
 * 禁止再新增散落色值。色值与 Design Token 的对应关系见各常量注释。
 *
 * 引入顺序约束：必须先于 amap-board.js / amap-thumb.js / amap-picker.js 加载。
 */
(function () {
    'use strict';

    window.AmapPalette = {
        // 地块多边形调色板（按索引循环使用），首色与品牌色 --app-brand-color 同值
        PLOT_PALETTE: ['#0052d9', '#00a870', '#ed7b2f', '#e34d59', '#834ec2',
            '#0594fa', '#ebb105', '#d4380d', '#13c2c2', '#eb2f96'],

        // 产区参考边界：琥珀色虚线描边 + 半透明琥珀底（与地块调色板区分）
        AREA_STROKE: '#fa8c16',
        AREA_FILL: '#faad14',
        AREA_FILL_OPACITY: 0.18,

        // 拉框绘制/选点多边形：品牌蓝（与 --app-brand-color 同值）
        DRAW_COLOR: '#0052d9',

        // 地块名称标注文字色：彩色标注底上的白字（对应 --app-text-anti）
        LABEL_TEXT: '#fff'
    };
})();
