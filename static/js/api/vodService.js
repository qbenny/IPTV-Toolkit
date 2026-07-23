/**
 * vodService — VOD 数据同步 / 配置 / 统计
 * 
 * 对应端点：
 *   POST /api/sync/start          — 启动全量 VOD 同步
 *   GET  /api/sync/status          — 同步进度状态
 *   GET  /api/sync/stats           — 数据库统计
 *   GET  /api/vod-config/config    — VOD 过滤设置
 *   PUT  /api/vod-config/config    — 更新 VOD 过滤设置
 */
const vodService = {
    /* ---------- 同步控制 ---------- */
    triggerSync:  () => ApiClient.post('/api/sync/start'),
    getStatus:    () => ApiClient.get('/api/sync/status'),
    getDbStats:   () => ApiClient.get('/api/sync/stats'),

    /* ---------- VOD 过滤 ---------- */
    getVodConfig:  () => ApiClient.get('/api/vod-config/config'),
    saveVodConfig: (cfg) => ApiClient.put('/api/vod-config/config', cfg),
};
