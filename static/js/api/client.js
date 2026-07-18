/**
 * ApiClient — IPTV-Toolkit 统一 HTTP 请求层
 * 
 * 职责：
 *   - 封装所有 fetch 调用，消除 45+ 处手写样板代码
 *   - 统一错误处理（ApiError 携带 HTTP 状态码和响应体）
 *   - 自动区分 JSON / FormData / 无 body 请求
 * 
 * 用法：
 *   const data = await ApiClient.get('/api/sim-status');
 *   const res  = await ApiClient.post('/api/stb-config', config);
 *   const res  = await ApiClient.put('/api/live/channels/1', update);
 *   const res  = await ApiClient.del('/api/live/channels/1');
 * 
 * 错误处理：
 *   try {
 *       const data = await ApiClient.get('/api/xxx');
 *   } catch (e) {
 *       if (e instanceof ApiError) {
 *           // e.status === HTTP 状态码
 *           // e.message === 服务端返回的消息或默认错误文本
 *           // e.data === 服务端返回的原始 JSON
 *       }
 *   }
 */

class ApiError extends Error {
    /**
     * @param {number} status HTTP 状态码
     * @param {object} data   服务端返回的 JSON 体
     */
    constructor(status, data) {
        const msg = data?.message || data?.detail || `请求失败 (HTTP ${status})`;
        super(msg);
        this.name = 'ApiError';
        this.status = status;
        this.data = data || {};
    }
}

const ApiClient = {
    /**
     * GET 请求
     * @param {string} path  - API 路径（如 '/api/sim-status'）
     * @returns {Promise<any>} 解析后的 JSON 数据
     */
    async get(path) {
        const r = await fetch(path);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new ApiError(r.status, data);
        return data;
    },

    /**
     * POST 请求
     * @param {string} path  - API 路径
     * @param {object|FormData|string|undefined} body - 请求体
     *   - undefined / null  → 无 Content-Type，空 body
     *   - FormData 实例    → multipart/form-data（由浏览器自动设置）
     *   - 其它 → JSON 序列化
     * @returns {Promise<any>} 解析后的 JSON 数据
     */
    async post(path, body) {
        const opts = { method: 'POST' };
        if (body === undefined || body === null) {
            // 无 body POST（如触发同步）
        } else if (body instanceof FormData) {
            // 文件上传，浏览器自动设置 Content-Type 和 boundary
            opts.body = body;
        } else {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(body);
        }
        const r = await fetch(path, opts);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new ApiError(r.status, data);
        return data;
    },

    /**
     * PUT 请求
     * @param {string} path  - API 路径
     * @param {object} body  - JSON 请求体
     * @returns {Promise<any>} 解析后的 JSON 数据
     */
    async put(path, body) {
        const r = await fetch(path, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new ApiError(r.status, data);
        return data;
    },

    /**
     * DELETE 请求
     * @param {string} path - API 路径
     * @returns {Promise<any>} 解析后的 JSON 数据
     */
    async del(path) {
        const r = await fetch(path, { method: 'DELETE' });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new ApiError(r.status, data);
        return data;
    }
};
