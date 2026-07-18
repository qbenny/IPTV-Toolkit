/**
 * VodTab — VOD 点播管理 Tab
 * 数据同步控制 / 数据库统计 / VOD过滤 / 同步说明
 * Inject: showToast, formatTime
 */
const VodTab = {
    name: 'VodTab',
    inject: ['showToast', 'formatTime'],
    emits: ['sync-complete'],

    data() {
        return {
            syncStatus: { running: false, progress: '', current_type: '', done: 0, total: 0, last_sync_time: null, last_error: null },
            dbStats: { total: 0, types: {} },
            syncStatusTimer: null,
            previousSyncRunning: false,

            vodConfig: { low_quality_filter_bool: true, m3u8_filter_bool: true }
        };
    },

    created() {
        this.fetchSyncStatus();
        this.fetchDbStats();
        this.fetchVodConfig();
        if (!this.syncStatusTimer) this.syncStatusTimer = setInterval(() => this.fetchSyncStatus(), 10000);
    },

    beforeUnmount() {
        if (this.syncStatusTimer) { clearInterval(this.syncStatusTimer); this.syncStatusTimer = null; }
    },

    methods: {
        async triggerSync() {
            try {
                const res = await vodService.triggerSync();
                if (res.status === 'started') { this.showToast('同步已启动'); this.fetchSyncStatus(); }
                else if (res.status === 'already_running') { this.showToast(res.message, 'error'); this.fetchSyncStatus(); }
                else this.showToast(res.message || '启动失败', 'error');
            } catch (e) { this.showToast(e.message || '通信异常', 'error'); }
        },
        async fetchSyncStatus() {
            try {
                this.syncStatus = await vodService.getStatus();
                if (!this.syncStatus.running && this.syncStatusTimer) {
                    clearInterval(this.syncStatusTimer); this.syncStatusTimer = null;
                    if (this.previousSyncRunning && this.syncStatus.last_sync_time) {
                        this.showToast('同步完成!'); this.fetchDbStats();
                    }
                }
                this.previousSyncRunning = this.syncStatus.running;
            } catch (e) { /* silent */ }
        },
        async fetchDbStats() { try { this.dbStats = await vodService.getDbStats(); } catch (e) {} },
        async fetchVodConfig() {
            try {
                const config = await vodService.getVodConfig();
                this.vodConfig.low_quality_filter_bool = config.low_quality_filter !== '0';
                this.vodConfig.m3u8_filter_bool = config.m3u8_filter !== '0';
            } catch (e) { console.warn('VOD配置加载失败', e); }
        },
        async saveVodConfig() {
            const payload = { low_quality_filter: this.vodConfig.low_quality_filter_bool ? '1' : '0', m3u8_filter: this.vodConfig.m3u8_filter_bool ? '1' : '0' };
            try { await vodService.saveVodConfig(payload); this.showToast('过滤设置已保存'); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        }
    },

    template: `
        <div class="card-grid">
            <div class="left-col">
                <div class="card">
                    <div class="card-header"><h3>数据同步控制</h3></div>
                    <div class="status-list mt-15">
                        <div class="status-item"><span class="status-label">同步状态</span><span :class="['status-val', syncStatus.running ? 'text-warning' : 'text-success']">{{ syncStatus.running ? '⏳ 同步中...' : (syncStatus.last_sync_time ? '✅ 已同步' : '⭕ 未同步') }}</span></div>
                        <div class="status-item" v-if="syncStatus.running"><span class="status-label">当前进度</span><span class="status-val highlight">{{ syncStatus.progress }}</span></div>
                        <div class="status-item" v-if="syncStatus.last_sync_time"><span class="status-label">上次同步</span><span class="status-val">{{ formatTime(syncStatus.last_sync_time) }}</span></div>
                        <div class="status-item" v-if="syncStatus.last_error"><span class="status-label">错误信息</span><span class="status-val text-error">{{ syncStatus.last_error }}</span></div>
                    </div>
                    <div class="form-actions mt-15"><button class="btn btn-primary w-full" @click="triggerSync" :disabled="syncStatus.running">{{ syncStatus.running ? '⏳ 同步中...' : '🔄 开始全量同步' }}</button></div>
                </div>
                <div class="card">
                    <div class="card-header"><h3>VOD 过滤设置</h3></div>
                    <div class="status-list mt-15">
                        <div class="form-group inline-checkbox-group mb-15"><div class="switch-item"><label class="switch-toggle"><input type="checkbox" v-model="vodConfig.low_quality_filter_bool"><span class="switch-slider"></span></label><span class="switch-label-text">🗑️ 过滤低质量内容</span></div><small class="form-help text-muted">屏蔽 电视剧/综艺 的 ep=0&sc=0 垃圾，纪录 无海报垃圾。电影/少儿/动漫 不受影响。重启 TVBox 生效。</small></div>
                        <div class="form-group inline-checkbox-group mb-15"><div class="switch-item"><label class="switch-toggle"><input type="checkbox" v-model="vodConfig.m3u8_filter_bool"><span class="switch-slider"></span></label><span class="switch-label-text">📦 屏蔽 m3u8 内容池</span></div><small class="form-help text-muted">屏蔽 JHT/YANHUA/YANKUM 三个池（~1645 条，格式不兼容）。重启 TVBox 生效。</small></div>
                        <div class="form-actions mt-15"><button class="btn btn-primary w-full" @click="saveVodConfig">💾 保存过滤设置</button></div>
                    </div>
                </div>
            </div>
            <div class="card">
                <div class="card-header"><h3>数据库统计</h3></div>
                <div class="status-list horizontal-stats mt-15">
                    <div class="status-item"><span class="status-label">总条目数</span><span class="status-val highlight">{{ dbStats.total }}</span></div>
                    <div class="status-item" v-for="(count, typeName) in dbStats.types" :key="typeName"><span class="status-label">{{ typeName }}</span><span class="status-val">{{ count }} 条</span></div>
                    <div v-if="dbStats.total === 0" class="status-item"><span class="status-label">数据库为空，请先执行同步</span></div>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><h3>同步说明</h3></div>
            <div class="status-list"><p class="status-desc">📥 数据来源：VIS API <code>api/search/filter.json</code><br>📊 同步类型：电视剧、电影、综艺、动漫、少儿<br>🔄 同步方式：全量覆盖（每日建议执行一次）<br>⚡ TVBox 接口基于本地 SQLite 数据库，支持 country / year 多条件过滤和评分排序</p></div>
        </div>
    `
};
