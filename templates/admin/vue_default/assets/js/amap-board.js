/**
 * 高德产区整体地图看板组件 amap-board — 全局可复用（产区详情页使用）
 *
 * 能力：
 * - 动态加载高德 Web JS API（webapi.amap.com，境内服务）+ 安全密钥注入
 * - 叠加「产区参考边界」（灰色只读轮廓）+ 多个地块多边形（调色板循环上色 + 名称标注）
 * - startDraw() 拉框绘制一个新地块，emit 'plot-drawn'：
 *     { boundary(GeoJSON), longitude, latitude(中心点), area_size(亩) }
 * - 越界软校验：新地块中心点落在产区参考边界外时仅 MessagePlugin.warning，不阻断
 * - setFitView 自适应展示全部要素
 *
 * Key/安全码由父页面 GET /map-config 注入（props: apiKey / securityCode），组件不硬编码。
 *
 * 用法：
 *   <amap-board :api-key="mapKey" :security-code="mapSecurity"
 *       :area-boundary="area.boundary" :plots="plots" :center="[lng,lat]"
 *       @plot-drawn="onPlotDrawn"></amap-board>
 *
 * 依赖：由 hy-app.js 的 createPage 统一 app.component 注册；分隔符沿用 [[ ]]。
 */
(function (window) {
    'use strict';

    // 高德 JS API 加载状态（与 amap-picker 独立缓存同一个 window.AMap，插件按需合并）
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
                '&plugin=AMap.MouseTool,AMap.GeometryUtil,AMap.PolygonEditor';
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

    // 多边形路径 → GeoJSON Polygon 字符串（首尾闭合）
    function pathToGeoJson(path) {
        if (!path || !path.length) { return ''; }
        var coords = path.map(function (p) {
            var lng = (typeof p.getLng === 'function') ? p.getLng() : (p.lng !== undefined ? p.lng : p[0]);
            var lat = (typeof p.getLat === 'function') ? p.getLat() : (p.lat !== undefined ? p.lat : p[1]);
            return [lng, lat];
        });
        if (coords.length && (coords[0][0] !== coords[coords.length - 1][0] ||
            coords[0][1] !== coords[coords.length - 1][1])) {
            coords.push(coords[0]);
        }
        return JSON.stringify({ type: 'Polygon', coordinates: [coords] });
    }

    // 路径中心点（简单算术平均，用于标注/回填经纬度）
    function pathCenter(path) {
        if (!path || !path.length) { return null; }
        var sx = 0, sy = 0, n = 0;
        path.forEach(function (p) {
            var lng = (typeof p.getLng === 'function') ? p.getLng() : (p.lng !== undefined ? p.lng : p[0]);
            var lat = (typeof p.getLat === 'function') ? p.getLat() : (p.lat !== undefined ? p.lat : p[1]);
            sx += lng; sy += lat; n += 1;
        });
        return n ? [sx / n, sy / n] : null;
    }

    // 射线法：点 [lng,lat] 是否在多边形路径 [[lng,lat],...] 内
    function pointInPolygon(pt, path) {
        if (!pt || !path || path.length < 3) { return true; }
        var x = pt[0], y = pt[1], inside = false;
        for (var i = 0, j = path.length - 1; i < path.length; j = i++) {
            var xi = path[i][0], yi = path[i][1];
            var xj = path[j][0], yj = path[j][1];
            var intersect = ((yi > y) !== (yj > y)) &&
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
            if (intersect) { inside = !inside; }
        }
        return inside;
    }

    // 计算点 pt 到线段 [a, b] 的最近点（投影+端点限制）
    function nearestPointOnSegment(pt, a, b) {
        var dx = b[0] - a[0], dy = b[1] - a[1];
        var lenSq = dx * dx + dy * dy;
        if (lenSq === 0) { return a; }
        var t = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / lenSq;
        t = Math.max(0, Math.min(1, t));
        return [a[0] + t * dx, a[1] + t * dy];
    }

    // 将路径中超出产区边界的顶点吸附到最近边界点，返回 { clipped, hasClipped }
    function clipToAreaBoundary(path, areaPath) {
        if (!areaPath || areaPath.length < 3) { return { clipped: path, hasClipped: false }; }
        var hasClipped = false;
        var clipped = path.map(function (pt) {
            var lng = (typeof pt.getLng === 'function') ? pt.getLng() : (pt.lng !== undefined ? pt.lng : pt[0]);
            var lat = (typeof pt.getLat === 'function') ? pt.getLat() : (pt.lat !== undefined ? pt.lat : pt[1]);
            var p = [lng, lat];
            if (pointInPolygon(p, areaPath)) { return p; }
            // 超出边界，找最近的边界点
            var minDist = Infinity, nearest = p;
            for (var i = 0; i < areaPath.length; i++) {
                var j = (i + 1) % areaPath.length;
                var np = nearestPointOnSegment(p, areaPath[i], areaPath[j]);
                var d = (np[0] - p[0]) * (np[0] - p[0]) + (np[1] - p[1]) * (np[1] - p[1]);
                if (d < minDist) { minDist = d; nearest = np; }
            }
            hasClipped = true;
            return nearest;
        });
        return { clipped: clipped, hasClipped: hasClipped };
    }

    var AmapBoard = {
        name: 'AmapBoard',
        props: {
            apiKey: { type: String, default: '' },
            securityCode: { type: String, default: '' },
            // 产区参考边界 GeoJSON 字符串（可空：无边界则跳过参考轮廓与越界校验）
            areaBoundary: { type: String, default: '' },
            // 已有地块数组 [{ id, name, boundary }]
            plots: { type: Array, default: function () { return []; } },
            // 地块内硬件标记数组 [{ id, plot_id, marker_longitude, marker_latitude, image_url, device_type }]
            hardwareMarkers: { type: Array, default: function () { return []; } },
            // 初始中心点 [lng, lat]（产区无边界时用于定位）
            center: { type: Array, default: function () { return []; } }
        },
        emits: ['plot-drawn', 'plot-updated', 'hardware-marker-moved'],
        setup: function (props, ctx) {
            var mapRef = Vue.ref(null);
            var loadError = Vue.ref('');
            var loading = Vue.ref(false);
            var drawing = Vue.ref(false);
            var editing = Vue.ref(false);        // 是否处于「已有地块顶点编辑」态
            var MessagePlugin = TDesign.MessagePlugin;

            // 非响应式地图实例引用
            var mapObj = null, mouseTool = null;
            var areaPolygon = null;              // 产区参考边界
            var plotOverlays = [];               // 已有地块多边形 + 文本标注
            var hardwareMarkerGroup = null;      // 独立模块管理的硬件水滴标记
            var areaPath = [];                   // 产区参考边界路径（越界校验用）
            var polyEditor = null;               // 多边形顶点编辑器（AMap.PolygonEditor）
            var editingPoly = null, editingId = 0; // 当前正在编辑的地块多边形与其 ID
            var drawTargetId = 0;                // 拉框绘制目标：0=新建地块，>0=为已有无边界地块补画边界

            // 渲染产区参考边界（琥珀色只读轮廓 + 半透明底色，凸显产区范围，区别于地块调色板）
            function renderAreaBoundary() {
                if (areaPolygon) { mapObj.remove(areaPolygon); areaPolygon = null; }
                areaPath = geoJsonToPath(props.areaBoundary);
                if (!areaPath.length) { return; }
                areaPolygon = new window.AMap.Polygon({
                    path: areaPath.map(function (c) { return [c[0], c[1]]; }),
                    strokeColor: window.AmapPalette.AREA_STROKE, strokeWeight: 3, strokeStyle: 'dashed',
                    fillColor: window.AmapPalette.AREA_FILL,
                    fillOpacity: window.AmapPalette.AREA_FILL_OPACITY, bubble: true
                });
                mapObj.add(areaPolygon);
            }

            // 渲染全部已有地块（不同颜色 + 名称标注）
            function renderPlots() {
                plotOverlays.forEach(function (o) { mapObj.remove(o); });
                plotOverlays = [];
                (props.plots || []).forEach(function (plot, idx) {
                    var path = geoJsonToPath(plot.boundary);
                    if (!path.length) { return; }
                    var color = PALETTE[idx % PALETTE.length];
                    var poly = new window.AMap.Polygon({
                        path: path.map(function (c) { return [c[0], c[1]]; }),
                        strokeColor: color, strokeWeight: 2,
                        fillColor: color, fillOpacity: 0.35
                    });
                    mapObj.add(poly);
                    plotOverlays.push(poly);
                    // 点击已有地块 → 进入顶点编辑态（拖动顶点修改边界）
                    (function (curPlot, curPoly) {
                        curPoly.on('click', function () { startEditPlot(curPlot, curPoly); });
                    })(plot, poly);
                    // 地块名称标注（居中）
                    var center = pathCenter(path);
                    if (center && plot.name) {
                        var text = new window.AMap.Text({
                            text: plot.name, position: center,
                            style: {
                                'background-color': color, 'border-color': color,
                                color: window.AmapPalette.LABEL_TEXT, 'font-size': '12px', padding: '2px 6px',
                                'border-radius': '2px'
                            }
                        });
                        mapObj.add(text);
                        plotOverlays.push(text);
                    }
                });
                renderHardwareMarkers();
            }

            // 硬件标记可在绑定地块内拖动，拖动完成后向父页面回传经纬度持久化。
            function renderHardwareMarkers() {
                if (hardwareMarkerGroup) { hardwareMarkerGroup.dispose(); hardwareMarkerGroup = null; }
                if (!window.HuiYanHardwareMarker) { return; }
                hardwareMarkerGroup = window.HuiYanHardwareMarker.renderMarkers({
                    AMap: window.AMap, map: mapObj, plots: props.plots,
                    markers: props.hardwareMarkers, draggable: true,
                    onMoved: function (device, position) {
                        ctx.emit('hardware-marker-moved', {
                            id: device.id,
                            marker_longitude: position[0],
                            marker_latitude: position[1]
                        });
                    },
                    onRejected: function () {
                        MessagePlugin.warning('设备标记只能放在绑定地块内');
                    }
                });
            }

            // 自适应展示全部要素
            function fitAll() {
                var all = [];
                if (areaPolygon) { all.push(areaPolygon); }
                plotOverlays.forEach(function (o) { all.push(o); });
                if (all.length) { mapObj.setFitView(all); }
            }

            function initMap() {
                if (!props.apiKey) {
                    loadError.value = '未配置高德地图 Key，请前往「地图设置」填写后使用地图看板';
                    return;
                }
                loading.value = true;
                loadAMap(props.apiKey, props.securityCode).then(function (AMap) {
                    var center = (props.center && props.center.length === 2)
                        ? props.center : [116.397428, 39.90923];
                    mapObj = new AMap.Map(mapRef.value, {
                        zoom: 14, center: center,
                        layers: [new AMap.TileLayer.Satellite(), new AMap.TileLayer.RoadNet()]
                    });
                    mouseTool = new AMap.MouseTool(mapObj);
                    polyEditor = new AMap.PolygonEditor(mapObj);
                    mouseTool.on('draw', function (e) {
                        var poly = e.obj;
                        var path = poly.getPath();
                        // 顶点越界硬校验 + 吸附
                        var clipResult = clipToAreaBoundary(path, areaPath);
                        var finalPath = clipResult.clipped;
                        if (clipResult.hasClipped) {
                            MessagePlugin.info('已将超出产区边界的顶点自动吸附到边界上');
                        }
                        var geo = pathToGeoJson(finalPath);
                        var center = pathCenter(finalPath);
                        // 亩 = 平方米 / 666.6667（1 亩 ≈ 666.67 ㎡）
                        var area = 0;
                        try { area = AMap.GeometryUtil.ringArea(finalPath.map(function(c){ return new AMap.LngLat(c[0],c[1]); })) / 666.6667; } catch (e2) { area = 0; }
                        // 画完即移除临时图形，交由父级刷新后统一重绘
                        mapObj.remove(poly);
                        mouseTool.close(false);
                        drawing.value = false;
                        var areaSize = Math.round(area * 100) / 100;
                        if (drawTargetId) {
                            // 为已有无边界地块补画边界：走 plot-updated（父级 PUT 全量提交回填）
                            var tid = drawTargetId;
                            drawTargetId = 0;
                            ctx.emit('plot-updated', { id: tid, boundary: geo, area_size: areaSize });
                        } else {
                            ctx.emit('plot-drawn', {
                                boundary: geo,
                                longitude: center ? center[0] : 0,
                                latitude: center ? center[1] : 0,
                                area_size: areaSize
                            });
                        }
                    });
                    renderAreaBoundary();
                    renderPlots();
                    setTimeout(function () {
                        if (!mapObj) { return; }
                        mapObj.resize();
                        fitAll();
                    }, 300);
                }).catch(function (err) {
                    loadError.value = err.message || '地图加载失败';
                }).finally(function () {
                    loading.value = false;
                });
            }

            // 开始拉框绘制一个新地块
            function startDraw() {
                if (!mouseTool) { return; }
                drawTargetId = 0;
                drawing.value = true;
                mouseTool.polygon({
                    strokeColor: window.AmapPalette.DRAW_COLOR, strokeWeight: 2,
                    fillColor: window.AmapPalette.DRAW_COLOR, fillOpacity: 0.3
                });
            }

            // 为已有无边界地块补画边界（列表「补画边界」按钮触发）：拉框完成后走 plot-updated 回填
            function startDrawForPlot(plotId) {
                if (!mouseTool || drawing.value || editing.value) { return; }
                drawTargetId = plotId || 0;
                drawing.value = true;
                mouseTool.polygon({
                    strokeColor: window.AmapPalette.DRAW_COLOR, strokeWeight: 2,
                    fillColor: window.AmapPalette.DRAW_COLOR, fillOpacity: 0.3
                });
            }

            // 点击已有地块进入顶点编辑态（拖动顶点即可修改边界）
            function startEditPlot(plot, poly) {
                if (drawing.value || !polyEditor || editing.value) { return; }
                editingId = plot.id;
                editingPoly = poly;
                polyEditor.setTarget(poly);
                polyEditor.open();
                editing.value = true;
            }

            // 保存边界编辑：取当前顶点路径 → GeoJSON + 面积，emit 给父页面全量提交
            function saveEdit() {
                if (!polyEditor || !editingPoly) { return; }
                var path = editingPoly.getPath();
                // 顶点越界硬校验 + 吸附
                var clipResult = clipToAreaBoundary(path, areaPath);
                var finalPath = clipResult.clipped;
                if (clipResult.hasClipped) {
                    MessagePlugin.info('已将超出产区边界的顶点自动吸附到边界上');
                }
                var geo = pathToGeoJson(finalPath);
                var area = 0;
                try { area = window.AMap.GeometryUtil.ringArea(finalPath.map(function(c){ return new window.AMap.LngLat(c[0],c[1]); })) / 666.6667; } catch (e) { area = 0; }
                polyEditor.close();
                var id = editingId;
                editing.value = false; editingId = 0; editingPoly = null;
                ctx.emit('plot-updated', {
                    id: id, boundary: geo, area_size: Math.round(area * 100) / 100
                });
            }

            // 取消编辑：关闭编辑器并按原数据重绘（丢弃未保存的顶点变更）
            function cancelEdit() {
                if (polyEditor) { polyEditor.close(); }
                editing.value = false; editingId = 0; editingPoly = null;
                renderPlots(); fitAll();
            }

            // plots / areaBoundary 变化时重绘
            Vue.watch(function () { return props.plots; }, function () {
                if (!mapObj) { return; }
                renderPlots();
                fitAll();
            }, { deep: true });
            Vue.watch(function () { return props.hardwareMarkers; }, function () {
                if (mapObj) { renderHardwareMarkers(); }
            }, { deep: true });
            Vue.watch(function () { return props.areaBoundary; }, function () {
                if (!mapObj) { return; }
                renderAreaBoundary();
                fitAll();
            });

            Vue.onMounted(function () { initMap(); });
            Vue.onBeforeUnmount(function () {
                if (hardwareMarkerGroup) { hardwareMarkerGroup.dispose(); hardwareMarkerGroup = null; }
                if (mapObj) { try { mapObj.destroy(); } catch (e) { /* 忽略 */ } mapObj = null; }
            });

            return {
                mapRef: mapRef, loadError: loadError, loading: loading, drawing: drawing,
                editing: editing, startDraw: startDraw, startDrawForPlot: startDrawForPlot,
                saveEdit: saveEdit, cancelEdit: cancelEdit
            };
        },
        template:
            '<div class="amap-board">' +
            '  <div v-if="loadError" class="amap-board__tip">[[ loadError ]]</div>' +
            '  <template v-else>' +
            '    <div class="amap-board__toolbar">' +
            '      <template v-if="editing">' +
            '        <t-button theme="primary" @click="saveEdit">保存边界</t-button>' +
            '        <t-button theme="default" @click="cancelEdit">取消</t-button>' +
            '        <span class="amap-board__hint">拖动顶点修改边界，拖动边中点可新增顶点，完成后点「保存边界」。</span>' +
            '      </template>' +
            '      <template v-else>' +
            '        <t-button :theme="drawing ? \'warning\' : \'primary\'" @click="startDraw">[[ drawing ? \'绘制中…（在图上拉框）\' : \'新增地块（拉框）\' ]]</t-button>' +
            '        <span class="amap-board__hint">在产区范围内拉框绘制新地块；点击已有地块可拖动顶点修改边界。</span>' +
            '      </template>' +
            '    </div>' +
            '    <div ref="mapRef" class="amap-board__map" v-loading="loading"></div>' +
            '  </template>' +
            '</div>'
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['amap-board'] = AmapBoard;
})(window);
