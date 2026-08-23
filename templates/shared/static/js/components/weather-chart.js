/* 天气页图表数据转换，独立于页面请求和 Tab 状态。 */
(function (window) {
    'use strict';
    window.WeatherCharts = {
        buildGddSeries: function (rows, baseTemp) {
            var series = { dates: [], avgs: [], actives: [], effectives: [] };
            var active = 0;
            var effective = 0;
            rows.forEach(function (row) {
                var avg = row.temp_avg;
                if (avg == null && row.temp_max != null && row.temp_min != null) {
                    avg = (row.temp_max + row.temp_min) / 2;
                }
                if (avg != null && avg >= baseTemp) {
                    active += avg;
                    effective += avg - baseTemp;
                }
                series.dates.push(row.date);
                series.avgs.push(avg == null ? null : avg);
                series.actives.push(Math.round(active * 10) / 10);
                series.effectives.push(Math.round(effective * 10) / 10);
            });
            return series;
        }
    };
})(window);
