/**
 * 产区管理插件 - 演示状态控制器。
 * 承载两个演示能力，独立成文件以控制 index.js 单文件行数：
 * 1. 重置状态：恢复 2026-09-17 演示日志卡片并删除其派生任务（可反复重放）；
 * 2. 生成任务前的智能体推演弹窗：根据当前日志动态生成分步推演内容，约 10 秒后走真实生成接口。
 * 由 plugin.json 先于 index.js 加载，index.js 通过 window.PAMDemo 接线。
 */
(function (window) {
    'use strict';

    // 推演节奏：单步标题停留 + 说明展开，5 步合计约 10 秒
    var STEP_HOLD = 1100;
    var STEP_DETAIL = 900;
    var FINAL_HOLD = 700;

    // 依据当前日志上下文构造推演步骤，内容随日志数据变化，不写死指标
    function buildThinkingSteps(ctx) {
        ctx = ctx || {};
        var weatherDesc = '读取产区当日天气实况';
        if (ctx.weather_text) {
            weatherDesc = '天气' + ctx.weather_text + '，湿度 ' + ctx.humidity + '%，降水 ' + ctx.precip + 'mm';
        }
        return [
            { title: '解析事实日志', desc: (ctx.area_name || '产区') + ' · ' + (ctx.fact_date || '') + ' 产区事实' },
            { title: '调用工具 weather.daily_fact', desc: weatherDesc },
            { title: '调用工具 crop.growth_stage', desc: '识别地块作物生育期' + (ctx.stage ? '：' + ctx.stage : '') },
            { title: '模型思考', desc: '结合生育期特征与天气实况，评估现场风险并匹配整改措施' },
            { title: '生成整改任务', desc: '整理整改措施写入任务台账，等待人工执行' },
        ];
    }

    function createDemoAddons(options) {
        var Vue = window.Vue;
        var message = options.message;
        var api = options.api;
        var reload = options.reload;
        var generate = options.generate;
        var demoThinkingVisible = Vue.ref(false);
        var demoThinkingStep = Vue.ref(-1);
        var demoThinkingDetail = Vue.ref(false);
        var demoResetting = Vue.ref(false);
        var timer = null;

        var clearTimer = function () {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        };

        // 由打开中的日志详情构造推演上下文
        var currentContext = function () {
            var log = options.logDetail && options.logDetail.value && options.logDetail.value.log;
            if (!log) return null;
            var weather = log.weather || {};
            return {
                area_name: log.area_name,
                fact_date: log.fact_date,
                weather_text: weather.text_day,
                humidity: weather.humidity,
                precip: weather.precip,
                stage: log.stage,
            };
        };

        // 推演视图：把步骤序列映射为 完成/进行中/待执行 三种状态
        var demoThinkingSteps = Vue.computed(function () {
            var steps = buildThinkingSteps(demoThinkingVisible.value ? currentContext() : null);
            return steps.map(function (step, index) {
                var state = 'pending';
                if (demoThinkingStep.value > index) {
                    state = 'done';
                } else if (demoThinkingStep.value === index) {
                    state = 'active';
                }
                return {
                    title: step.title,
                    desc: step.desc,
                    state: state,
                    showDesc: index <= demoThinkingStep.value,
                };
            });
        });

        // 点击“根据建议生成整改任务”：先分步推演，结束再走真实生成接口
        var runDemoThinking = function () {
            if (demoThinkingVisible.value) return;
            demoThinkingStep.value = -1;
            demoThinkingDetail.value = false;
            demoThinkingVisible.value = true;
            var stepCount = buildThinkingSteps(currentContext()).length;
            var advance = function () {
                demoThinkingStep.value += 1;
                if (demoThinkingStep.value >= stepCount) {
                    timer = setTimeout(function () {
                        clearTimer();
                        demoThinkingVisible.value = false;
                        generate();
                    }, FINAL_HOLD);
                    return;
                }
                timer = setTimeout(function () {
                    demoThinkingDetail.value = true;
                    timer = setTimeout(advance, STEP_DETAIL);
                }, STEP_HOLD);
            };
            advance();
        };

        // 重置演示状态：恢复 2026-09-17 演示日志并清空其派生任务
        var resetDemoStatus = async function () {
            if (demoResetting.value) return;
            demoResetting.value = true;
            try {
                await api.post('/demo/reset');
                message.success('状态已重置');
                await reload();
            } finally {
                demoResetting.value = false;
            }
        };

        return {
            bindings: {
                demoThinkingVisible: demoThinkingVisible,
                demoThinkingSteps: demoThinkingSteps,
                demoResetting: demoResetting,
                runDemoThinking: runDemoThinking,
                resetDemoStatus: resetDemoStatus,
            },
            dispose: clearTimer,
        };
    }

    window.PAMDemo = {
        createDemoAddons: createDemoAddons,
    };
})(window);
