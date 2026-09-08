/** 地块边界硬件标记的共享几何与渲染工具。 */
(function (window) {
    'use strict';

    function geoJsonToPath(boundary) {
        if (!boundary) return [];
        try {
            var geo = typeof boundary === 'string' ? JSON.parse(boundary) : boundary;
            if (geo && geo.type === 'Polygon' && geo.coordinates && geo.coordinates[0]) {
                return normalizePath(geo.coordinates[0]);
            }
        } catch (e) { /* 非法边界由地图组件按空路径处理 */ }
        return [];
    }

    function normalizePath(path) {
        var result = (path || []).map(function (point) {
            if (Array.isArray(point)) return [Number(point[0]), Number(point[1])];
            var lng = typeof point.getLng === 'function' ? point.getLng() : point.lng;
            var lat = typeof point.getLat === 'function' ? point.getLat() : point.lat;
            return [Number(lng), Number(lat)];
        }).filter(function (point) { return Number.isFinite(point[0]) && Number.isFinite(point[1]); });
        if (result.length > 1) {
            var first = result[0], last = result[result.length - 1];
            if (first[0] === last[0] && first[1] === last[1]) result.pop();
        }
        return result;
    }

    function segmentLength(a, b) {
        var middleLatitude = (a[1] + b[1]) * Math.PI / 360;
        var dx = (b[0] - a[0]) * Math.cos(middleLatitude);
        var dy = b[1] - a[1];
        return Math.sqrt(dx * dx + dy * dy);
    }

    function buildSegments(path) {
        var points = normalizePath(path);
        var segments = [], total = 0;
        if (points.length < 2) return { segments: segments, total: total };
        points.forEach(function (start, index) {
            var end = points[(index + 1) % points.length];
            var length = segmentLength(start, end);
            segments.push({ start: start, end: end, length: length, offset: total });
            total += length;
        });
        return { segments: segments, total: total };
    }

    function pointAtRatio(path, ratio) {
        var built = buildSegments(path);
        if (!built.total) return null;
        var target = (((Number(ratio) || 0) % 1) + 1) % 1 * built.total;
        for (var i = 0; i < built.segments.length; i++) {
            var segment = built.segments[i];
            if (target <= segment.offset + segment.length || i === built.segments.length - 1) {
                var t = segment.length ? (target - segment.offset) / segment.length : 0;
                return [
                    segment.start[0] + (segment.end[0] - segment.start[0]) * t,
                    segment.start[1] + (segment.end[1] - segment.start[1]) * t,
                ];
            }
        }
        return built.segments[0].start;
    }

    function pointOnSegment(point, start, end) {
        var cross = (point[1] - start[1]) * (end[0] - start[0]) -
            (point[0] - start[0]) * (end[1] - start[1]);
        if (Math.abs(cross) > 1e-10) return false;
        return point[0] >= Math.min(start[0], end[0]) - 1e-10 &&
            point[0] <= Math.max(start[0], end[0]) + 1e-10 &&
            point[1] >= Math.min(start[1], end[1]) - 1e-10 &&
            point[1] <= Math.max(start[1], end[1]) + 1e-10;
    }

    function pointInPolygon(point, path) {
        var points = normalizePath(path);
        if (points.length < 3) return false;
        var inside = false, previous = points[points.length - 1];
        for (var index = 0; index < points.length; index++) {
            var current = points[index];
            if (pointOnSegment(point, previous, current)) return true;
            if ((current[1] > point[1]) !== (previous[1] > point[1])) {
                var crossing = (previous[0] - current[0]) *
                    (point[1] - current[1]) / (previous[1] - current[1]) + current[0];
                if (point[0] < crossing) inside = !inside;
            }
            previous = current;
        }
        return inside;
    }

    function polygonCentroid(path) {
        var areaTwice = 0, longitudeSum = 0, latitudeSum = 0;
        path.forEach(function (start, index) {
            var end = path[(index + 1) % path.length];
            var cross = start[0] * end[1] - end[0] * start[1];
            areaTwice += cross;
            longitudeSum += (start[0] + end[0]) * cross;
            latitudeSum += (start[1] + end[1]) * cross;
        });
        if (Math.abs(areaTwice) > 1e-12) {
            return [longitudeSum / (3 * areaTwice), latitudeSum / (3 * areaTwice)];
        }
        return pathCenter(path);
    }

    function gridInteriorPoint(path, preferred) {
        var lngs = path.map(function (point) { return point[0]; });
        var lats = path.map(function (point) { return point[1]; });
        var minLng = Math.min.apply(null, lngs), maxLng = Math.max.apply(null, lngs);
        var minLat = Math.min.apply(null, lats), maxLat = Math.max.apply(null, lats);
        var candidates = [];
        for (var x = 1; x < 12; x++) {
            for (var y = 1; y < 12; y++) {
                candidates.push([
                    minLng + (maxLng - minLng) * x / 12,
                    minLat + (maxLat - minLat) * y / 12,
                ]);
            }
        }
        candidates.sort(function (left, right) {
            var ld = (left[0] - preferred[0]) ** 2 + (left[1] - preferred[1]) ** 2;
            var rd = (right[0] - preferred[0]) ** 2 + (right[1] - preferred[1]) ** 2;
            return ld - rd;
        });
        return candidates.find(function (point) { return pointInPolygon(point, path); }) || null;
    }

    function defaultInsidePoint(path, legacyRatio) {
        var points = normalizePath(path);
        if (points.length < 3) return null;
        var centroid = polygonCentroid(points);
        var preferred = pointInPolygon(centroid, points)
            ? centroid : gridInteriorPoint(points, centroid);
        if (!preferred) return points[0];
        if (legacyRatio === null || legacyRatio === undefined) return preferred;
        var edge = pointAtRatio(points, legacyRatio);
        if (!edge) return preferred;
        var progresses = [0.2, 0.35, 0.5, 0.75, 1];
        for (var index = 0; index < progresses.length; index++) {
            var progress = progresses[index];
            var candidate = [
                edge[0] + (preferred[0] - edge[0]) * progress,
                edge[1] + (preferred[1] - edge[1]) * progress,
            ];
            if (pointInPolygon(candidate, points)) return candidate;
        }
        return preferred;
    }

    function resolvePosition(path, device) {
        var hasCoordinates = device.marker_longitude !== null &&
            device.marker_longitude !== undefined &&
            device.marker_latitude !== null && device.marker_latitude !== undefined;
        var longitude = Number(device.marker_longitude), latitude = Number(device.marker_latitude);
        if (hasCoordinates && Number.isFinite(longitude) && Number.isFinite(latitude)) {
            var saved = [longitude, latitude];
            if (pointInPolygon(saved, path)) return saved;
        }
        return defaultInsidePoint(path, device.marker_ratio);
    }

    function pathCenter(path) {
        var points = normalizePath(path);
        if (!points.length) return null;
        var sum = points.reduce(function (value, point) {
            return [value[0] + point[0], value[1] + point[1]];
        }, [0, 0]);
        return [sum[0] / points.length, sum[1] / points.length];
    }

    function iconComponent(type) {
        var names = {
            growth: 'ImageIcon', soil: 'ChartBubbleIcon', root: 'TreeRoundDotIcon',
        };
        var icons = window.TDesignIconVueNext || {};
        return icons[names[type]] || icons.ControlPlatformIcon || icons.SettingIcon;
    }

    function mountFallback(host, type) {
        var component = iconComponent(type);
        if (!component || !window.Vue) return null;
        var app = window.Vue.createApp({
            render: function () { return window.Vue.h(component, { size: '22px' }); },
        });
        app.mount(host);
        return app;
    }

    function createContent(device, draggable) {
        var root = document.createElement('div');
        root.className = 'hardware-map-marker' + (draggable ? ' is-draggable' : '');
        if (device.nickname || device.device_name) root.title = device.nickname || device.device_name;
        var drop = document.createElement('div');
        drop.className = 'hardware-map-marker__drop';
        var fallbackApp = null;
        var showFallback = function () {
            drop.replaceChildren();
            var fallback = document.createElement('div');
            fallback.className = 'hardware-map-marker__fallback';
            drop.appendChild(fallback);
            fallbackApp = mountFallback(fallback, device.device_type);
        };
        if (device.image_url) {
            var image = document.createElement('img');
            image.className = 'hardware-map-marker__media';
            image.alt = '';
            image.draggable = false;
            image.src = device.image_url;
            image.addEventListener('error', showFallback, { once: true });
            drop.appendChild(image);
        } else {
            showFallback();
        }
        root.appendChild(drop);
        return { element: root, dispose: function () { if (fallbackApp) fallbackApp.unmount(); } };
    }

    function renderMarkers(options) {
        var plotPaths = {};
        (options.plots || []).forEach(function (plot) {
            plotPaths[plot.id] = geoJsonToPath(plot.boundary);
        });
        var entries = [];
        (options.markers || []).forEach(function (device) {
            var path = plotPaths[device.plot_id];
            var position = resolvePosition(path, device);
            if (!position) return;
            var content = createContent(device, !!options.draggable);
            var marker = new options.AMap.Marker({
                position: position, content: content.element,
                anchor: 'bottom-center', draggable: !!options.draggable,
                zIndex: 180,
            });
            options.map.add(marker);
            if (options.draggable) {
                marker.on('dragend', function (event) {
                    var lngLat = event.lnglat || marker.getPosition();
                    var point = [lngLat.getLng(), lngLat.getLat()];
                    if (!pointInPolygon(point, path)) {
                        marker.setPosition(position);
                        if (options.onRejected) options.onRejected(device);
                        return;
                    }
                    position = point;
                    if (options.onMoved) options.onMoved(device, point);
                });
            }
            entries.push({ marker: marker, dispose: content.dispose });
        });
        return {
            dispose: function () {
                entries.forEach(function (entry) {
                    options.map.remove(entry.marker); entry.dispose();
                });
                entries = [];
            },
        };
    }

    window.HuiYanHardwareMarker = {
        geoJsonToPath: geoJsonToPath,
        pathCenter: pathCenter,
        pointAtRatio: pointAtRatio,
        pointInPolygon: pointInPolygon,
        defaultInsidePoint: defaultInsidePoint,
        resolvePosition: resolvePosition,
        renderMarkers: renderMarkers,
    };
})(window);
