/**
 * 高德只读缩略地图组件 amap-thumb — 全局可复用（农户绑定页产区卡片缩略图 / 农户端产区地图用）
 *
 * 能力：
 * - 动态加载高德 Web JS API（webapi.amap.com，境内服务）+ 安全密钥注入（与 amap-board 共享 window.AMap）
 * - 只读展示「产区参考边界」（琥珀色虚线轮廓）+ 全部地块多边形（调色板循环上色 + 名称标注）
 * - 默认全交互禁用（禁止拖拽/缩放/滚轮/双击/键盘），纯缩略预览；setFitView 自适应展示全部要素
 * - interactive=true 时开启简易浏览交互（拖拽/滚轮缩放/双击缩放/手势缩放 + 缩放工具条），仍不可编辑要素
 * - 无边界且无地块时显示占位文案，不渲染空白地图
 *
 * Key/安全码由父页面 GET /map-config 注入（props: apiKey / securityCode），组件不硬编码。
 *
 * 用法：
 *   <amap-thumb :api-key="mapKey" :security-code="mapSecurity"
 *       :area-boundary="geo.boundary" :plots="geo.plots" :center="geo.center"></amap-thumb>
 *   可交互只读地图（农户端产区地图）追加 :interactive="true"
 *
 * 依赖：管理端由 hy-app.js 的 createPage 统一 app.component 注册；
 *       农户端页面通过 HuiYanFarmer.createApp 的 components 选项手动注册；分隔符沿用 [[ ]]。
 */
(function (window) {
    'use strict';

    // 高德 JS API 加载状态（各组件独立缓存，共享 window.AMap）
    // 插件集与 amap-board/amap-picker 保持一致，避免不同组件间重复注入 maps 脚本
    var _amapLoading = null;

    function loadAMap(apiKey, securityCode) {
        if (window.AMap && window.AMap.MouseTool) { return Promise.resolve(window.AMap); }
        if (_amapLoading) { return _amapLoading; }
        if (securityCode) {
            window._AMapSecurityConfig = { securityJsCode: securityCode };
        }
        _amapLoading = new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.type = 'text/javascript';
            script.src = 'https://webapi.amap.com/maps?v=2.0&key=' + encodeURIComponent(apiKey) +
                '&plugin=AMap.MouseTool,AMap.GeometryUtil,AMap.PolygonEditor,AMap.ToolBar';
            script.onerror = function () { reject(new Error('高德地图脚本加载失败')); };
            script.onload = function () {
                if (window.AMap) { resolve(window.AMap); }
                else { reject(new Error('高德地图对象未就绪')); }
            };
            document.head.appendChild(script);
        });
        return _amapLoading;
    }

    // 地块多边形调色板（循环使用），集中维护在 amap-palette.js，与产区参考边界（琥珀色）区分
    var PALETTE = window.AmapPalette.PLOT_PALETTE;

    // GeoJSON Polygon 字符串 → 路径 [[lng,lat],...]
    function geoJsonToPath(boundary) {
        if (!boundary) { return []; }
        try {
            var geo = JSON.parse(boundary);
            if (geo && geo.type === 'Polygon' && geo.coordinates && geo.coordinates[0]) {
                return geo.coordinates[0];
            }
        } catch (e) { /* 忽略非法 GeoJSON */ }
        return [];
    }

    // 路径中心点（简单算术平均，用于地块名称标注定位）
    function pathCenter(path) {
        if (!path || !path.length) { return null; }
        var sx = 0, sy = 0, n = 0;
        path.forEach(function (p) { sx += p[0]; sy += p[1]; n += 1; });
        return n ? [sx / n, sy / n] : null;
    }

    var AmapThumb = {
        name: 'AmapThumb',
        props: {
            apiKey: { type: String, default: '' },
            securityCode: { type: String, default: '' },
            // 产区参考边界 GeoJSON 字符串（可空）
            areaBoundary: { type: String, default: '' },
            // 已有地块数组 [{ name, boundary }]
            plots: { type: Array, default: function () { return []; } },
            // 只读地块内硬件标记
            hardwareMarkers: { type: Array, default: function () { return []; } },
            // 中心点 [lng, lat]（无边界要素时用于定位；有要素时以 setFitView 为准）
            center: { type: Array, default: function () { return []; } },
            // 是否开启简易浏览交互（拖拽/缩放）；默认 false 保持纯只读缩略预览
            interactive: { type: Boolean, default: false }
        },
        setup: function (props) {
            var mapRef = Vue.ref(null);
            var loadError = Vue.ref('');
            // 是否有可渲染要素：产区边界或任一地块边界
            var hasGeo = Vue.computed(function () {
                if (props.areaBoundary) { return true; }
                return (props.plots || []).some(function (p) { return p && p.boundary; });
            });

            // 非响应式地图实例引用
            var mapObj = null;
            var hardwareMarkerGroup = null;
            var resizeObserver = null;          // 容器尺寸观察器：卡片/SPA 布局尺寸变有效时重算地图
            var destroyed = false;              // 组件是否已卸载：防止异步加载回调在卸载后创建孤儿地图实例

            // 渲染全部要素（产区参考边界 + 地块多边形 + 名称标注），并自适应展示
            function renderAll(AMap) {
                var overlays = [];
                var areaPath = geoJsonToPath(props.areaBoundary);
                if (areaPath.length) {
                    var areaPoly = new AMap.Polygon({
                        path: areaPath, strokeColor: window.AmapPalette.AREA_STROKE, strokeWeight: 2,
                        strokeStyle: 'dashed', fillColor: window.AmapPalette.AREA_FILL,
                        fillOpacity: window.AmapPalette.AREA_FILL_OPACITY
                    });
                    mapObj.add(areaPoly); overlays.push(areaPoly);
                }
                (props.plots || []).forEach(function (plot, idx) {
                    var path = geoJsonToPath(plot.boundary);
                    if (!path.length) { return; }
                    var color = PALETTE[idx % PALETTE.length];
                    var poly = new AMap.Polygon({
                        path: path, strokeColor: color, strokeWeight: 2,
                        fillColor: color, fillOpacity: 0.35
                    });
                    mapObj.add(poly); overlays.push(poly);
                    var c = pathCenter(path);
                    if (c && plot.name) {
                        var text = new AMap.Text({
                            text: plot.name, position: c,
                            style: {
                                'background-color': color, 'border-color': color,
                                color: window.AmapPalette.LABEL_TEXT, 'font-size': '12px', padding: '1px 4px',
                                'border-radius': '2px'
                            }
                        });
                        mapObj.add(text); overlays.push(text);
                    }
                });
                renderHardwareMarkers(AMap);
                // 自适应展示全部要素（immediately=true，四周留 8px 边距）
                if (overlays.length) { mapObj.setFitView(overlays, true, [8, 8, 8, 8]); }
            }

            function renderHardwareMarkers(AMap) {
                if (hardwareMarkerGroup) { hardwareMarkerGroup.dispose(); hardwareMarkerGroup = null; }
                if (!window.HuiYanHardwareMarker || !mapObj) { return; }
                hardwareMarkerGroup = window.HuiYanHardwareMarker.renderMarkers({
                    AMap: AMap, map: mapObj, plots: props.plots,
                    markers: props.hardwareMarkers, draggable: false
                });
            }

            function initMap() {
                if (!props.apiKey || !hasGeo.value) { return; }
                loadAMap(props.apiKey, props.securityCode).then(function (AMap) {
                    // 异步加载期间组件可能已卸载，此时不再建图，避免孤儿实例
                    if (destroyed || !mapRef.value) { return; }
                    var center = (props.center && props.center.length === 2)
                        ? props.center : [116.397428, 39.90923];
                    // 只读缩略：卫星影像底图 + 路网标注（比矢量道路图更直观展示地块地貌）
                    // interactive=true 时开启拖拽/缩放浏览交互，否则关闭全部交互（纯缩略预览）
                    var itv = !!props.interactive;
                    var layers = [];
                    if (AMap.TileLayer && AMap.TileLayer.Satellite) {
                        layers.push(new AMap.TileLayer.Satellite());
                        if (AMap.TileLayer.RoadNet) { layers.push(new AMap.TileLayer.RoadNet()); }
                    }
                    mapObj = new AMap.Map(mapRef.value, {
                        zoom: 13, center: center,
                        layers: layers.length ? layers : undefined,
                        dragEnable: itv, zoomEnable: itv, doubleClickZoom: itv,
                        keyboardEnable: itv, scrollWheel: itv, touchZoom: itv,
                        jogEnable: itv, animateEnable: itv
                    });
                    // 可交互时挂载缩放工具条（存在性守卫：AMap 若由旧 plugin 集加载则静默跳过）
                    if (itv && AMap.ToolBar) {
                        try { mapObj.addControl(new AMap.ToolBar()); } catch (e) { /* 忽略 */ }
                    }
                    renderAll(AMap);
                    // 卡片布局/SPA 切换可能导致容器初始尺寸不准，延时重算尺寸
                    setTimeout(function () {
                        if (mapObj) { mapObj.resize(); }
                    }, 300);
                    if (window.ResizeObserver && mapRef.value) {
                        resizeObserver = new window.ResizeObserver(function () {
                            if (mapObj) { mapObj.resize(); }
                        });
                        resizeObserver.observe(mapRef.value);
                    }
                }).catch(function (err) {
                    loadError.value = err.message || '地图加载失败';
                });
            }

            Vue.onMounted(function () { initMap(); });
            Vue.watch(function () { return props.hardwareMarkers; }, function () {
                if (mapObj) { renderHardwareMarkers(window.AMap); }
            }, { deep: true });
            Vue.onBeforeUnmount(function () {
                destroyed = true;
                if (hardwareMarkerGroup) { hardwareMarkerGroup.dispose(); hardwareMarkerGroup = null; }
                if (resizeObserver) { try { resizeObserver.disconnect(); } catch (e) { /* 忽略 */ } resizeObserver = null; }
                if (mapObj) { try { mapObj.destroy(); } catch (e) { /* 忽略 */ } mapObj = null; }
            });

            return { mapRef: mapRef, loadError: loadError, hasGeo: hasGeo };
        },
        template:
            '<div class="amap-thumb">' +
            '  <div v-if="loadError" class="amap-thumb__empty">[[ loadError ]]</div>' +
            '  <div v-else-if="!hasGeo" class="amap-thumb__empty">暂无边界</div>' +
            '  <div v-else ref="mapRef" class="amap-thumb__map"></div>' +
            '</div>'
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['amap-thumb'] = AmapThumb;
})(window);
