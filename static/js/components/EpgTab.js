/**
 * EpgTab — EPG 节目管理 Tab
 * 同步/统计/当前播放
 * Inject: showToast, formatTime
 */
const EpgTab = {
    name: 'EpgTab',
    inject: ['showToast', 'formatTime'],

    data() {
        return {
            epgSyncStatus: { running: false, progress: '', last_sync_time: null },
            epgSyncTimer: null,
            previousEpgRunning: false,
            epgStats: { total_programs: 0, total_channels: 0, date_range: null },
            nowPlaying: [],
            nowPlayingLoaded: false
        };
    },

    created() {
        this.fetchEpgSyncStatus();
        this.fetchEpgStats();
    },

    beforeUnmount() {
        if (this.epgSyncTimer) { clearInterval(this.epgSyncTimer); this.epgSyncTimer = null; }
    },

    methods: {
        async triggerEpgSync() {
            try {
                const res = await epgService.triggerSync();
                if (res.status === 'started') { this.showToast('EPG 同步已启动'); this.startEpgSyncPolling(); }
                else if (res.status === 'already_running') { this.showToast(res.message, 'error'); this.startEpgSyncPolling(); }
                else this.showToast(res.message || '启动失败', 'error');
            } catch (e) { this.showToast(e.message || '通信异常', 'error'); }
        },
        async fetchEpgSyncStatus() {
            try {
                this.epgSyncStatus = await epgService.getSyncStatus();
                if (!this.epgSyncStatus.running && this.epgSyncTimer) {
                    clearInterval(this.epgSyncTimer); this.epgSyncTimer = null;
                    if (this.previousEpgRunning && this.epgSyncStatus.last_sync_time) {
                        this.showToast('EPG 同步完成!'); this.fetchEpgStats();
                    }
                }
                this.previousEpgRunning = this.epgSyncStatus.running;
            } catch (e) {}
        },
        async fetchEpgStats() { try { this.epgStats = await epgService.getStats(); } catch (e) {} },
        startEpgSyncPolling() {
            this.fetchEpgSyncStatus(); this.fetchEpgStats();
            if (!this.epgSyncTimer) this.epgSyncTimer = setInterval(() => this.fetchEpgSyncStatus(), 2000);
        },
        async fetchNowPlaying() { try { const data = await epgService.getNowPlaying(); this.nowPlaying = data.items || []; this.nowPlayingLoaded = true; } catch (e) {} }
    },

    template: `
        <div class="card-grid">
            <div class="card"><div class="card-header"><h3>EPG 节目同步</h3></div>
                <div class="status-list mt-15">
                    <div class="status-item"><span class="status-label">同步状态</span><span :class="['status-val',epgSyncStatus.running?'text-warning':(epgSyncStatus.last_sync_time?'text-success':'text-muted')]">{{ epgSyncStatus.running?'同步中...':(epgSyncStatus.last_sync_time?'已同步':'未同步') }}</span></div>
                    <div class="status-item" v-if="epgSyncStatus.running"><span class="status-label">进度</span><span class="status-val highlight">{{ epgSyncStatus.progress }}</span></div>
                    <div class="status-item" v-if="epgSyncStatus.last_sync_time"><span class="status-label">上次同步</span><span class="status-val">{{ formatTime(epgSyncStatus.last_sync_time) }}</span></div>
                </div>
                <div class="form-actions mt-20"><button class="btn btn-primary w-full" @click="triggerEpgSync" :disabled="epgSyncStatus.running">{{ epgSyncStatus.running?'同步中...':'开始 EPG 同步' }}</button></div>
            </div>
            <div class="card"><div class="card-header"><h3>EPG 统计</h3></div>
                <div class="status-list mt-15">
                    <div class="status-item"><span class="status-label">节目总数</span><span class="status-val highlight">{{ epgStats.total_programs }}</span></div>
                    <div class="status-item"><span class="status-label">频道数</span><span class="status-val text-success">{{ epgStats.total_channels }}</span></div>
                    <div class="status-item"><span class="status-label">日期范围</span><span class="status-val">{{ epgStats.date_range?.earliest||'-' }} ~ {{ epgStats.date_range?.latest||'-' }}</span></div>
                </div>
                <p class="status-desc mt-16">VIS api/schedules/ | 无需认证 | 覆盖全频道 | 保留 9 天</p>
            </div>
        </div>
        <div class="card mt-20"><div class="card-header"><h3>当前正在播放</h3><button class="btn btn-secondary btn-sm" @click="fetchNowPlaying">刷新</button></div>
            <div class="live-ch-list mt-10" style="max-height:400px;overflow-y:auto;">
                <div v-if="nowPlaying.length===0" class="empty-state">{{ nowPlayingLoaded?'无正在播放节目':'点击刷新' }}</div>
                <div v-for="p in nowPlaying" :key="p.channel_id" style="padding:8px 12px;border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;align-items:center;font-size:14px;"><span><strong>{{ p.channel_name }}</strong><span style="color:var(--text-muted);margin-left:8px;">{{ p.title }}</span></span><span style="color:var(--text-muted);font-size:12px;">{{ p.start_time?.slice(11,16) }} - {{ p.end_time?.slice(11,16) }}</span></div>
            </div>
        </div>
    `
};
