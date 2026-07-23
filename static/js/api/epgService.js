/**
 * epgService — EPG 节目单同步 / 查询
 * 
 * 对应端点：
 *   POST /api/epg/sync                  — 启动 EPG 同步
 *   GET  /api/epg/sync/status           — EPG 同步进度
 *   GET  /api/epg/stats                 — EPG 统计
 *   GET  /api/epg/programs/now          — 当前播放
 *   GET  /api/epg/programs?channel_id=  — 频道节目查询
 */
const epgService = {
    /* ---------- 同步 ---------- */
    triggerSync:    () => ApiClient.post('/api/epg/sync'),
    getSyncStatus:  () => ApiClient.get('/api/epg/sync/status'),

    /* ---------- 查询 ---------- */
    getStats:        () => ApiClient.get('/api/epg/stats'),
    getNowPlaying:   () => ApiClient.get('/api/epg/programs/now'),
    getPrograms:     (channelId, date, limit = 200) =>
        ApiClient.get(`/api/epg/programs?channel_id=${encodeURIComponent(channelId)}&date=${date}&limit=${limit}`),
};
