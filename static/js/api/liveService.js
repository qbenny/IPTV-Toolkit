/**
 * liveService — 直播频道管理 / 分类 / 别名 / 导入
 * 
 * 对应端点：
 *   —— 频道 ——
 *   GET    /api/live/channels                     — 频道列表（支持查询参数）
 *   PUT    /api/live/channels/:id                 — 更新频道
 *   DELETE /api/live/channels/:id                 — 删除频道
 *   POST   /api/live/channels/batch-enabled       — 批量启用/禁用
 *   POST   /api/live/channels/batch-category     — 批量修改分类
 *   POST   /api/live/channels/batch-delete       — 批量删除
 *   POST   /api/live/channels/reorder            — 排序
 *   POST   /api/live/channels/reset-order        — 重置排序
 *   —— 配置 & 统计 ——
 *   GET    /api/live/config                       — 直播配置
 *   PUT    /api/live/config                       — 更新直播配置
 *   GET    /api/live/stats                        — 频道统计
 *   POST   /api/live/sync                         — 直播同步
 *   —— 分类 ——
 *   GET    /api/live/categories                   — 分类列表
 *   POST   /api/live/categories                   — 新增分类
 *   PUT    /api/live/categories/:id               — 更新分类
 *   DELETE /api/live/categories/:id               — 删除分类
 *   POST   /api/live/categories/reorder           — 排序分类
 *   —— 导入 ——
 *   POST   /api/live/import                       — 导入频道（JSON 或 FormData）
 *   —— 别名 ——
 *   GET    /api/live/aliases                      — 别名列表
 *   POST   /api/live/aliases                      — 新增别名
 *   PUT    /api/live/aliases/:id                  — 更新别名
 *   DELETE /api/live/aliases/:id                  — 删除别名
 *   POST   /api/live/aliases/reapply             — 重新应用别名
 *   GET    /api/live/aliases/export               — 导出别名
 *   POST   /api/live/aliases/import               — 导入别名
 *   —— 分类映射 ——
 *   GET    /api/live/categories/mappings/export   — 导出分类映射
 *   POST   /api/live/categories/mappings/import   — 导入分类映射
 */
const liveService = {
    /* ========================= 频道 ========================= */
    getChannels: (params = {}) => {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(params)) {
            if (v !== undefined && v !== null && v !== '') p.append(k, String(v));
        }
        return ApiClient.get(`/api/live/channels?${p.toString()}`);
    },

    updateChannel: (id, data) => ApiClient.put(`/api/live/channels/${id}`, data),

    deleteChannel: (id) => ApiClient.del(`/api/live/channels/${id}`),

    batchEnabled: (ids, enabled) =>
        ApiClient.post('/api/live/channels/batch-enabled', { ids, enabled }),

    batchCategory: (ids, category) =>
        ApiClient.post('/api/live/channels/batch-category', { ids, category }),

    batchDelete: (ids) =>
        ApiClient.post('/api/live/channels/batch-delete', { ids }),

    reorderChannels: (order) =>
        ApiClient.post('/api/live/channels/reorder', order),

    resetOrder: () => ApiClient.post('/api/live/channels/reset-order'),

    /* ========================= 配置 & 统计 ========================= */
    getConfig: () => ApiClient.get('/api/live/config'),

    saveConfig: (cfg) => ApiClient.put('/api/live/config', cfg),

    getStats: () => ApiClient.get('/api/live/stats'),

    triggerSync: () => ApiClient.post('/api/live/sync'),

    /* ========================= 分类 ========================= */
    getCategories: () => ApiClient.get('/api/live/categories'),

    addCategory: (data) => ApiClient.post('/api/live/categories', data),

    updateCategory: (id, data) => ApiClient.put(`/api/live/categories/${id}`, data),

    deleteCategory: (id) => ApiClient.del(`/api/live/categories/${id}`),

    reorderCategories: (order) =>
        ApiClient.post('/api/live/categories/reorder', order),

    /* ========================= 导入 ========================= */
    importChannels: (body) => ApiClient.post('/api/live/import', body),

    /* ========================= 别名 ========================= */
    getAliases: () => ApiClient.get('/api/live/aliases'),

    addAlias: (data) => ApiClient.post('/api/live/aliases', data),

    updateAlias: (id, data) => ApiClient.put(`/api/live/aliases/${id}`, data),

    deleteAlias: (id) => ApiClient.del(`/api/live/aliases/${id}`),

    reapplyAliases: () => ApiClient.post('/api/live/aliases/reapply'),

    exportAliases: () => ApiClient.get('/api/live/aliases/export'),

    importAliases: (body) => ApiClient.post('/api/live/aliases/import', body),

    /* ========================= 分类映射 ========================= */
    exportCategoryMappings: () => ApiClient.get('/api/live/categories/mappings/export'),

    importCategoryMappings: (body) =>
        ApiClient.post('/api/live/categories/mappings/import', body),
};
