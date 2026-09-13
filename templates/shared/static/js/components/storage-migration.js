/* 公共文件同步状态：管理端顶部栏和存储设置页共用同一份任务状态。 */
(function (global) {
    'use strict';

    const ACTIVE_PHASES = ['scanning', 'queued', 'running'];
    let sharedApi = null;

    function create(options) {
        if (sharedApi) return sharedApi;
        const { ref, computed } = Vue;
        const request = options.request;
        const migrationVisible = ref(false);
        const migrationTarget = ref(null);
        const migrationJob = ref(null);
        const migrationLoading = ref(false);
        const migratingStoragePlugin = ref('');
        const migrationRequestError = ref('');
        let migrationTimer = null;
        let hideTimer = null;
        let activeJobId = null;

        const unwrapNullable = (res) => {
            if (!res || !res.data || !Object.prototype.hasOwnProperty.call(res.data, 'data')) return null;
            return res.data.data;
        };
        const isActivePhase = (phase) => ACTIVE_PHASES.includes(phase);
        const isSuccessPhase = (phase) => ['finish', 'empty'].includes(phase);
        const migrationPercent = computed(() => {
            const job = migrationJob.value || {};
            if (isSuccessPhase(job.phase)) return 100;
            return job.total_files ? Math.round((job.processed_files || 0) * 100 / job.total_files) : 0;
        });
        const migrationPhaseLabel = computed(() => ({
            scanning: '正在扫描本地文件', awaiting: '正在恢复旧任务', queued: '等待上传', running: '上传中',
            finish: '同步完成', empty: '同步完成', partial: '上传失败', failed: '上传失败',
        }[(migrationJob.value || {}).phase] || (migrationRequestError.value ? '上传失败' : '未开始')));
        const migrationProgressStatus = computed(() => {
            const phase = (migrationJob.value || {}).phase;
            if (['partial', 'failed'].includes(phase) || migrationRequestError.value) return 'error';
            if (isSuccessPhase(phase)) return 'success';
            return 'active';
        });
        const formatStorageSize = (value) => {
            const size = Number(value) || 0;
            if (size < 1024) return size + ' B';
            if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
            if (size < 1024 * 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
            return (size / 1024 / 1024 / 1024).toFixed(1) + ' GB';
        };
        const state = computed(() => {
            const job = migrationJob.value || {};
            return {
                visible: migrationVisible.value,
                phase: job.phase || '',
                label: migrationPhaseLabel.value,
                percent: migrationPercent.value,
                progressStatus: migrationProgressStatus.value,
                processedFiles: job.processed_files || 0,
                totalFiles: job.total_files || 0,
                processedBytes: formatStorageSize(job.processed_bytes),
                totalBytes: formatStorageSize(job.total_bytes),
            };
        });
        const stopMigrationPolling = () => {
            if (migrationTimer) clearTimeout(migrationTimer);
            migrationTimer = null;
        };
        const clearHideTimer = () => {
            if (hideTimer) clearTimeout(hideTimer);
            hideTimer = null;
        };
        const hideSuccessfulState = (jobId) => {
            clearHideTimer();
            hideTimer = setTimeout(() => {
                if (activeJobId === jobId && isSuccessPhase((migrationJob.value || {}).phase)) {
                    migrationVisible.value = false;
                    migrationJob.value = null;
                    activeJobId = null;
                }
                hideTimer = null;
            }, 5000);
        };
        const applyJob = (job) => {
            migrationJob.value = job || null;
            migrationVisible.value = Boolean(job);
            migrationRequestError.value = '';
            if (!job) return;
            if (isSuccessPhase(job.phase)) {
                stopMigrationPolling();
                migratingStoragePlugin.value = '';
                hideSuccessfulState(job.id);
            } else if (['partial', 'failed'].includes(job.phase)) {
                stopMigrationPolling();
                migratingStoragePlugin.value = '';
                clearHideTimer();
            }
        };
        const fetchMigrationJob = (jobId) => request.get('/oss/migrations/' + jobId, { skipAutoError: true })
            .then((res) => {
                const data = unwrapNullable(res) || {};
                applyJob(data);
                return data;
            });
        const pollMigrationJob = (jobId) => {
            stopMigrationPolling();
            const poll = () => {
                if (activeJobId !== jobId) return;
                migrationTimer = setTimeout(() => {
                    migrationTimer = null;
                    fetchMigrationJob(jobId).then((job) => {
                        if (activeJobId === jobId && isActivePhase(job.phase)) poll();
                    }).catch((error) => {
                        const message = error?.response?.data?.msg || error?.message || '读取同步任务进度失败';
                        migrationRequestError.value = message;
                        migrationJob.value = Object.assign({}, migrationJob.value || {}, {
                            id: jobId, phase: 'failed', error_msg: message,
                        });
                        migrationVisible.value = true;
                        activeJobId = null;
                        migratingStoragePlugin.value = '';
                        stopMigrationPolling();
                    });
                }, 2000);
            };
            poll();
        };
        const start = (row) => {
            if (!row || !row.name) return Promise.resolve(null);
            if (migrationLoading.value || isActivePhase((migrationJob.value || {}).phase)) {
                return Promise.resolve(migrationJob.value);
            }
            clearHideTimer();
            stopMigrationPolling();
            migrationTarget.value = row;
            migrationVisible.value = true;
            migrationLoading.value = true;
            migratingStoragePlugin.value = row.name;
            migrationRequestError.value = '';
            migrationJob.value = null;
            activeJobId = null;
            return request.post('/oss/migrations/scan', null, {
                params: { target_method: row.name }, skipAutoError: true,
            }).then((res) => {
                const data = unwrapNullable(res) || {};
                if (!data.job_id) throw new Error('同步任务未返回任务编号');
                activeJobId = data.job_id;
                return fetchMigrationJob(data.job_id);
            }).then((job) => {
                if (isActivePhase(job.phase)) pollMigrationJob(job.id);
                return job;
            }).catch((error) => {
                const message = error?.response?.data?.msg || error?.message || '提交本地文件同步失败';
                migrationRequestError.value = message;
                migrationJob.value = { id: activeJobId, phase: 'failed', error_msg: message };
                migrationVisible.value = true;
                activeJobId = null;
                stopMigrationPolling();
                return migrationJob.value;
            }).finally(() => {
                migrationLoading.value = false;
                if (!isActivePhase((migrationJob.value || {}).phase)) migratingStoragePlugin.value = '';
            });
        };
        const restore = () => request.get('/oss/migrations/latest', { skipAutoError: true })
            .then((res) => {
                const job = unwrapNullable(res);
                if (!job || !job.id || !ACTIVE_PHASES.includes(job.phase)) return null;
                activeJobId = job.id;
                applyJob(job);
                if (isActivePhase(job.phase)) {
                    migratingStoragePlugin.value = job.target_method || '';
                    pollMigrationJob(job.id);
                }
                return job;
            }).catch(() => null);

        sharedApi = {
            state, migrationVisible, migrationTarget, migrationJob, migrationLoading, migratingStoragePlugin,
            migrationRequestError, migrationPercent, migrationPhaseLabel, migrationProgressStatus,
            formatStorageSize, start, restore, fetchMigrationJob, stopMigrationPolling,
            openStorageMigration: start,
        };
        return sharedApi;
    }

    global.HuiYanStorageMigration = { create };
})(window);
