/**
 * stbService — STB 机顶盒鉴权配置 / 仿真状态 / 系统日志
 * 
 * 对应端点：
 *   GET  /api/stb-config          — 读取 STB 配置
 *   POST /api/stb-config          — 保存并验证 STB 配置
 *   GET  /api/sim-status          — 仿真鉴权运行状态
 *   GET  /api/scheduler/config    — 定时调度器配置
 *   PUT  /api/scheduler/config    — 更新定时调度器配置
 *   GET  /api/scheduler/status    — 定时调度器运行状态
 *   GET  /api/logs?lines=&level=  — 读取系统日志
 *   POST /api/logs/clear          — 清空系统日志
 */
const stbService = {
    /* ---------- STB 鉴权 ---------- */
    getStbConfig:   () => ApiClient.get('/api/stb-config'),
    saveStbConfig:  (cfg) => ApiClient.post('/api/stb-config', cfg),
    getSimStatus:   () => ApiClient.get('/api/sim-status'),

    /* ---------- 定时调度器 ---------- */
    getSchedulerConfig: () => ApiClient.get('/api/scheduler/config'),
    getSchedulerStatus: () => ApiClient.get('/api/scheduler/status'),
    saveSchedulerConfig: (cfg) => ApiClient.put('/api/scheduler/config', cfg),

    /* ---------- 系统日志 ---------- */
    getLogs: (lines = 200, level = 'ALL') => ApiClient.get(`/api/logs?lines=${lines}&level=${level}`),
    clearLogs: () => ApiClient.post('/api/logs/clear'),
};
