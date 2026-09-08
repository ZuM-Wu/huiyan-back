/**
 * 高德地图选址组件 amap-picker — 全局可复用（产区/地块编辑弹窗内使用）
 *
 * 能力：
 * - 动态加载高德 Web JS API（webapi.amap.com，境内服务）+ 安全密钥注入
 * - AMap.PlaceSearch 地址搜索定位、点击地图选中心点回填经纬度
 * - AMap.MouseTool/PolygonEditor 拉框绘制多边形，输出 GeoJSON boundary
 * - v-model 双向绑定 { longitude, latitude, boundary }，与「手动填写」Tab 同步
 *
 * Key/安全码由父页面 GET /map-config 注入（props: apiKey / securityCode），组件不硬编码；
 * 未配置 apiKey 时组件显示提示、仅保留手填 Tab（父页面控制隐藏）。
 *
 * 用法：
 *   <amap-picker v-model="form.geo" :api-key="mapKey" :security-code="mapSecurity"></amap-picker>
 *   其中 form.geo = { longitude, latitude, boundary }
 *
 * 依赖：由 hy-app.js 的 createPage 统一 app.component 注册；分隔符沿用 [[ ]]。
 */
(function (window) {
    'use strict';

    // 高德 JS API 加载状态（模块级共享，避免重复注入 script）
    var _amapLoading = null;

    // 动态加载高德 JS API（含安全密钥），返回 Promise<AMap>
    function loadAMap(apiKey, securityCode) {
        // 已加载且所需插件就绪，直接返回
        if (window.AMap && window.AMap.PlaceSearch && window.AMap.MouseTool) {
            return Promise.resolve(window.AMap);
        }
        // AMap 已存在但缺少插件（如 amap-board 先加载了不含 PlaceSearch 的版本），动态补加载
        if (window.AMap && !window.AMap.PlaceSearch) {
            if (_amapLoading) { return _amapLoading; }
            _amapLoading = new Promise(function (resolve, reject) {
                var needed = [];
                if (!window.AMap.PlaceSearch) needed.push('AMap.PlaceSearch');
                if (!window.AMap.Geocoder) needed.push('AMap.Geocoder');
                if (!window.AMap.MouseTool) needed.push('AMap.MouseTool');
                if (!window.AMap.PolygonEditor) needed.push('AMap.PolygonEditor');
                if (!window.AMap.GeometryUtil) needed.push('AMap.GeometryUtil');
                if (needed.length === 0) { resolve(window.AMap); return; }
                window.AMap.plugin(needed, function () {
                    resolve(window.AMap);
                });
            });
            return _amapLoading;
        }
        if (_amapLoading) { return _amapLoading; }
        // 安全密钥须在加载 API 前配置
        if (securityCode) {
            window._AMapSecurityConfig = { securityJsCode: securityCode };
        }
        _amapLoading = new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.type = 'text/javascript';
            script.src = 'https://webapi.amap.com/maps?v=2.0&key=' + encodeURIComponent(apiKey) +
                '&plugin=AMap.PlaceSearch,AMap.MouseTool,AMap.PolygonEditor,AMap.Geocoder,AMap.GeometryUtil';
            script.onerror = function () { reject(new Error('高德地图脚本加载失败')); };
            script.onload = function () {
                if (window.AMap) { resolve(window.AMap); }
                else { reject(new Error('高德地图对象未就绪')); }
            };
            document.head.appendChild(script);
        });
        return _amapLoading;
    }

    // 多边形路径 [[lng,lat],...] → GeoJSON Polygon 字符串
    function pathToGeoJson(path) {
        if (!path || !path.length) { return ''; }
        var coords = path.map(function (p) {
            // 兼容 AMap.LngLat 对象与 [lng,lat] 数组
            var lng = (typeof p.getLng === 'function') ? p.getLng() : (p.lng !== undefined ? p.lng : p[0]);
            var lat = (typeof p.getLat === 'function') ? p.getLat() : (p.lat !== undefined ? p.lat : p[1]);
            return [lng, lat];
        });
        // GeoJSON 首尾闭合
        if (coords.length && (coords[0][0] !== coords[coords.length - 1][0] ||
            coords[0][1] !== coords[coords.length - 1][1])) {
            coords.push(coords[0]);
        }
        return JSON.stringify({ type: 'Polygon', coordinates: [coords] });
    }

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

    var AmapPicker = {
        name: 'AmapPicker',
        props: {
            // v-model：{ longitude, latitude, boundary }
            modelValue: { type: Object, default: function () { return {}; } },
            apiKey: { type: String, default: '' },
            securityCode: { type: String, default: '' },
            // 搜索初始城市（可选）
            city: { type: String, default: '全国' }
        },
        emits: ['update:modelValue'],
        setup: function (props, ctx) {
            var mapRef = Vue.ref(null);
            var keyword = Vue.ref('');
            var loadError = Vue.ref('');
            var loading = Vue.ref(false);
            var drawing = Vue.ref(false);
            var hasBoundary = Vue.ref(false);   // 当前是否已有边界（工具栏状态感知）
            var MessagePlugin = TDesign.MessagePlugin;

            // 非响应式的地图实例引用
            var mapObj = null, marker = null, polygon = null, mouseTool = null, placeSearch = null, geocoder = null;
            var polyEditor = null;              // 多边形顶点编辑器（AMap.PolygonEditor）
            var resizeObserver = null;          // 容器尺寸观察器：弹窗/Tab 动画结束尺寸变有效时重算地图
            var destroyed = false;              // 组件是否已卸载：防止异步加载回调在卸载后创建孤儿地图实例
            // 防止自发更新回环（组件 emit → 父级 → modelValue 变化 → watcher）
            var selfEmitting = false;
            var lastBoundary = '';

            // 回传数据给父级（合并现有值），并标记为自发更新避免 watcher 重绘
            function emitValue(patch) {
                selfEmitting = true;
                var next = Object.assign({}, props.modelValue || {}, patch);
                ctx.emit('update:modelValue', next);
                Vue.nextTick(function () { selfEmitting = false; });
            }

            // 逆地理编码：经纬度 → 省/市/区 + 详细地址，回填手动填写 Tab
            function reverseGeocode(lng, lat) {
                if (!geocoder) { return; }
                geocoder.getAddress([lng, lat], function (status, result) {
                    if (status === 'complete' && result.regeocode) {
                        var comp = result.regeocode.addressComponent || {};
                        // 直辖市时 city 为空，回退为 province
                        var city = comp.city || comp.province || '';
                        emitValue({
                            province: comp.province || '',
                            city: city,
                            district: comp.district || '',
                            address: result.regeocode.formattedAddress || ''
                        });
                    }
                });
            }

            // 在指定经纬度放置/移动中心点标记
            function setMarker(lng, lat) {
                if (!mapObj) { return; }
                if (!marker) {
                    marker = new window.AMap.Marker({ position: [lng, lat], draggable: true });
                    mapObj.add(marker);
                    marker.on('dragend', function (e) {
                        var p = e.lnglat;
                        emitValue({ longitude: p.getLng(), latitude: p.getLat() });
                        reverseGeocode(p.getLng(), p.getLat());
                    });
                } else {
                    marker.setPosition([lng, lat]);
                }
                mapObj.setCenter([lng, lat]);
            }

            // 取当前多边形顶点 → GeoJSON + 面积（亩），回写父级表单（编辑时即时生效）
            function syncPolygon() {
                if (!polygon) { return; }
                var path = polygon.getPath();
                var geo = pathToGeoJson(path);
                lastBoundary = geo;
                var areaSize = 0;
                try { areaSize = Math.round(window.AMap.GeometryUtil.ringArea(path) / 666.6667 * 100) / 100; } catch (e) { areaSize = 0; }
                emitValue({ boundary: geo, area_size: areaSize });
            }

            // 让多边形可编辑：点击进入顶点编辑态（拖动顶点/边中点即时回写）
            function attachEditable(poly) {
                if (!poly) { return; }
                poly.on('click', function () {
                    if (drawing.value || !polyEditor) { return; }
                    polyEditor.setTarget(poly);
                    polyEditor.open();
                });
            }

            // 根据已有 boundary 绘制多边形
            function renderBoundary(boundary) {
                lastBoundary = boundary || '';
                var path = geoJsonToPath(boundary);
                if (!path.length || !mapObj) { hasBoundary.value = false; return; }
                if (polygon) { mapObj.remove(polygon); polygon = null; }
                polygon = new window.AMap.Polygon({
                    path: path.map(function (c) { return [c[0], c[1]]; }),
                    strokeColor: window.AmapPalette.DRAW_COLOR,
                    fillColor: window.AmapPalette.DRAW_COLOR, fillOpacity: 0.2
                });
                mapObj.add(polygon);
                attachEditable(polygon);
                hasBoundary.value = true;
                mapObj.setFitView([polygon]);
            }

            // 初始化地图
            function initMap() {
                if (!props.apiKey) {
                    loadError.value = '未配置高德地图 Key，请前往「地图设置」填写后使用地图选址';
                    return;
                }
                loading.value = true;
                loadAMap(props.apiKey, props.securityCode).then(function (AMap) {
                    // 异步加载期间组件可能已因重新挂载（geoTabsKey/geoKey 变化）而卸载，此时不再建图，避免孤儿实例
                    if (destroyed || !mapRef.value) { return; }
                    var v = props.modelValue || {};
                    var center = (v.longitude && v.latitude) ? [v.longitude, v.latitude] : [116.397428, 39.90923];
                    // 底图默认卫星影像+路网标注（JSAPI 2.0 内置图层，境内服务），便于看清农田地块
                    mapObj = new AMap.Map(mapRef.value, {
                        zoom: 12, center: center,
                        layers: [new AMap.TileLayer.Satellite(), new AMap.TileLayer.RoadNet()]
                    });
                    // 地址搜索
                    placeSearch = new AMap.PlaceSearch({ map: mapObj, city: props.city });
                    // 逆地理编码器（点选/拖动/搜索后回填省市区地址）
                    geocoder = new AMap.Geocoder({ city: '全国' });
                    // 点击地图选中心点
                    mapObj.on('click', function (e) {
                        setMarker(e.lnglat.getLng(), e.lnglat.getLat());
                        emitValue({ longitude: e.lnglat.getLng(), latitude: e.lnglat.getLat() });
                        reverseGeocode(e.lnglat.getLng(), e.lnglat.getLat());
                    });
                    // 拉框绘制工具
                    mouseTool = new AMap.MouseTool(mapObj);
                    // 顶点编辑器：点击已有边界可拖动顶点修改，编辑事件即时回写
                    polyEditor = new AMap.PolygonEditor(mapObj);
                    polyEditor.on('adjust', syncPolygon);
                    polyEditor.on('addnode', syncPolygon);
                    polyEditor.on('removenode', syncPolygon);
                    polyEditor.on('end', syncPolygon);
                    mouseTool.on('draw', function (e) {
                        if (polygon) { mapObj.remove(polygon); }
                        polygon = e.obj;
                        var path = polygon.getPath();
                        var geo = pathToGeoJson(path);
                        lastBoundary = geo;
                        // 依绘制多边形自动计算面积（亩 = 平方米 / 666.6667），回填父级表单总面积
                        var areaSize = 0;
                        try { areaSize = Math.round(AMap.GeometryUtil.ringArea(path) / 666.6667 * 100) / 100; } catch (e2) { areaSize = 0; }
                        emitValue({ boundary: geo, area_size: areaSize });
                        attachEditable(polygon);
                        hasBoundary.value = true;
                        mouseTool.close(false);
                        drawing.value = false;
                    });
                    // 回显已有中心点与边界
                    if (v.longitude && v.latitude) { setMarker(v.longitude, v.latitude); }
                    if (v.boundary) { renderBoundary(v.boundary); }
                    // 弹窗/Tab 开启动画可能导致容器初始尺寸不准，延时重算尺寸并自适应
                    setTimeout(function () {
                        if (!mapObj) { return; }
                        mapObj.resize();
                        if (v.boundary && polygon) { mapObj.setFitView([polygon]); }
                        else if (v.longitude && v.latitude) { mapObj.setCenter([v.longitude, v.latitude]); }
                    }, 300);
                    // 持续监听容器尺寸变化（弹窗动画结束、Tab 由隐变显、窗口缩放）：
                    // 容器从 0 尺寸变为有效尺寸时重算地图，修复初始 0 尺寸导致不渲染需手动刷新的问题
                    if (window.ResizeObserver && mapRef.value) {
                        resizeObserver = new window.ResizeObserver(function () {
                            if (mapObj) { mapObj.resize(); }
                        });
                        resizeObserver.observe(mapRef.value);
                    }
                }).catch(function (err) {
                    loadError.value = err.message || '地图加载失败';
                }).finally(function () {
                    loading.value = false;
                });
            }

            // 地址搜索：POI 搜索无结果时回退到地理编码，兼容街道/村组等非 POI 地址。
            function applySearchLocation(location) {
                if (!location) { return false; }
                var lng = typeof location.getLng === 'function' ? location.getLng() : location[0];
                var lat = typeof location.getLat === 'function' ? location.getLat() : location[1];
                if (lng === undefined || lat === undefined) { return false; }
                setMarker(lng, lat);
                emitValue({ longitude: lng, latitude: lat });
                reverseGeocode(lng, lat);
                return true;
            }

            function searchByGeocoder() {
                if (!geocoder) {
                    MessagePlugin.warning('地图正在加载，请稍后再试');
                    return;
                }
                geocoder.getLocation(keyword.value.trim(), function (status, result) {
                    var geocodes = result && result.geocodes;
                    if (status === 'complete' && geocodes && geocodes.length && applySearchLocation(geocodes[0].location)) {
                        return;
                    }
                    MessagePlugin.warning('未搜索到相关地点');
                });
            }

            function doSearch() {
                var value = keyword.value.trim();
                if (!value) {
                    MessagePlugin.warning('请输入搜索地址');
                    return;
                }
                if (!placeSearch) {
                    searchByGeocoder();
                    return;
                }
                placeSearch.search(value, function (status, result) {
                    var pois = result && result.poiList && result.poiList.pois;
                    if (status === 'complete' && pois && pois.length && applySearchLocation(pois[0].location)) {
                        return;
                    }
                    searchByGeocoder();
                });
            }

            // 开始拉框绘制多边形
            function startDraw() {
                if (!mouseTool) { return; }
                drawing.value = true;
                mouseTool.polygon({
                    strokeColor: window.AmapPalette.DRAW_COLOR,
                    fillColor: window.AmapPalette.DRAW_COLOR, fillOpacity: 0.2
                });
            }

            // 清除已绘制边界
            function clearBoundary() {
                if (polyEditor) { try { polyEditor.close(); } catch (e) { /* 忽略 */ } }
                if (polygon && mapObj) { mapObj.remove(polygon); polygon = null; }
                lastBoundary = '';
                hasBoundary.value = false;
                emitValue({ boundary: '' });
            }

            // 外部 modelValue 变化（如手动填写 Tab 改经纬度）时同步地图；自发更新跳过
            Vue.watch(function () { return props.modelValue; }, function (v) {
                if (selfEmitting || !mapObj || !v) { return; }
                if (v.longitude && v.latitude) { setMarker(v.longitude, v.latitude); }
                if ((v.boundary || '') !== lastBoundary) {
                    if (v.boundary) { renderBoundary(v.boundary); }
                    else if (polygon) { mapObj.remove(polygon); polygon = null; lastBoundary = ''; hasBoundary.value = false; }
                }
            }, { deep: true });

            Vue.onMounted(function () { initMap(); });
            Vue.onBeforeUnmount(function () {
                destroyed = true;
                if (resizeObserver) { try { resizeObserver.disconnect(); } catch (e) { /* 忽略 */ } resizeObserver = null; }
                if (polyEditor) { try { polyEditor.close(); } catch (e) { /* 忽略 */ } polyEditor = null; }
                if (mapObj) { try { mapObj.destroy(); } catch (e) { /* 忽略 */ } mapObj = null; }
            });

            return {
                mapRef: mapRef, keyword: keyword, loadError: loadError,
                loading: loading, drawing: drawing, hasBoundary: hasBoundary,
                doSearch: doSearch, startDraw: startDraw, clearBoundary: clearBoundary
            };
        },
        template:
            '<div class="amap-picker">' +
            '  <div v-if="loadError" class="amap-picker__tip">[[ loadError ]]</div>' +
            '  <template v-else>' +
            '    <div class="amap-picker__toolbar">' +
            '      <t-input v-model="keyword" placeholder="搜索地址定位" clearable style="width:240px" @enter="doSearch"></t-input>' +
            '      <t-button theme="primary" variant="outline" @click="doSearch">搜索</t-button>' +
            '      <t-button :theme="drawing ? \'warning\' : \'default\'" variant="outline" @click="startDraw">[[ drawing ? \'绘制中…\' : (hasBoundary ? \'重新拉框画边界\' : \'拉框画边界\') ]]</t-button>' +
            '      <t-button theme="default" variant="outline" @click="clearBoundary">清除边界</t-button>' +
            '    </div>' +
            '    <div ref="mapRef" class="amap-picker__map" v-loading="loading"></div>' +
            '    <div class="amap-picker__hint">[[ hasBoundary ? \'点击已有边界可拖动顶点/边中点修改，面积自动重算；点击地图可选中心点\' : \'点击地图选中心点（可拖动标记），或搜索地址定位；「拉框画边界」绘制地块多边形\' ]]</div>' +
            '  </template>' +
            '</div>'
    };

    window.HuiYanComponents = window.HuiYanComponents || {};
    window.HuiYanComponents['amap-picker'] = AmapPicker;
})(window);
