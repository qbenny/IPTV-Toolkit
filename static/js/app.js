/**
 * IPTV-Toolkit v2.0 前端逻辑
 * - 系统凭证配置
 * - 数据同步管理
 * - 系统日志查看
 */
const { createApp } = Vue;

const app = createApp({
    data() {
        return {
            activeTab: 'stb',
            theme: 'dark',

            // Toast
            toast: { show: false, message: '', type: 'success', timeoutId: null },

            // Tab info
            tabTitles: {
                stb: '系统凭证配置',
                sync: '数据同步管理',
                log: '系统日志'
            },
            tabSubtitles: {
                stb: '配置电信机顶盒仿真认证参数，保障安全接入 EPG 网关',
                sync: '从 VIS API 同步点播数据到本地 SQLite 数据库',
                log: '查看系统运行日志，支持级别过滤'
            },

            // Plate 1: STB Config
            stbConfig: {
                user_id: '', stb_id: '', mac_address: '',
                base_url: '', des_key: '', ip_address: ''
            },
            resolvedIp: '',
            savingStb: false,
            simStatus: { is_authenticated: false, epg_base_url: null, user_token: null, jsessionid: null },
            simStatusTimer: null,

            // Plate 2: Sync
            syncStatus: {
                running: false, progress: '', current_type: '',
                done: 0, total: 0, last_sync_time: null, last_error: null
            },
            dbStats: { total: 0, types: {}, last_synced: 0 },
            syncStatusTimer: null,

            // Plate 3: Log
            logs: [],
            logLevelFilter: 'ALL',
            logAutoScroll: true,
            logPollTimer: null
        };
    },

    computed: {
        filteredLogs() {
            if (this.logLevelFilter === 'ALL') return this.logs;
            const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
            const minIdx = levels.indexOf(this.logLevelFilter);
            return this.logs.filter(log => {
                const idx = levels.indexOf(log.level);
                return idx >= minIdx;
            });
        },
        vodApiLinks() {
            const origin = window.location.origin;
            return { tvbox: `${origin}/zjvod`, api: `${origin}/api/vod` };
        }
    },

    watch: {
        activeTab(newTab) {
            this.stopAllPolling();
            if (newTab === 'stb') {
                this.startSimStatusPolling();
            } else if (newTab === 'log') {
                this.startLogPolling();
            } else if (newTab === 'sync') {
                this.startSyncStatusPolling();
            }
        }
    },

    created() {
        this.initTheme();
        this.fetchStbConfig();
        this.fetchDbStats();
        if (this.activeTab === 'stb') this.startSimStatusPolling();
    },

    beforeUnmount() {
        this.stopAllPolling();
    },

    methods: {
        // ---- Theme ----
        initTheme() {
            const saved = localStorage.getItem('theme') || 'dark';
            this.theme = saved;
            document.documentElement.setAttribute('data-theme', saved);
        },
        toggleTheme() {
            const n = this.theme === 'dark' ? 'light' : 'dark';
            this.theme = n;
            localStorage.setItem('theme', n);
            document.documentElement.setAttribute('data-theme', n);
        },

        // ---- Toast ----
        showToast(msg, type = 'success') {
            if (this.toast.timeoutId) clearTimeout(this.toast.timeoutId);
            this.toast.show = true;
            this.toast.message = msg;
            this.toast.type = type;
            this.toast.timeoutId = setTimeout(() => { this.toast.show = false; }, 3000);
        },

        // ---- Polling ----
        stopAllPolling() {
            [this.simStatusTimer, this.syncStatusTimer, this.logPollTimer].forEach(t => {
                if (t) { clearInterval(t); }
            });
            this.simStatusTimer = this.syncStatusTimer = this.logPollTimer = null;
        },

        // ---- STB Config ----
        async fetchStbConfig() {
            try {
                const r = await fetch('/api/stb-config');
                this.stbConfig = await r.json();
            } catch (e) { /* silent */ }
        },

        async fetchSimStatus() {
            try {
                const r = await fetch('/api/sim-status');
                if (r.ok) {
                    const d = await r.json();
                    this.simStatus = d;
                    if (d.ip_address) this.resolvedIp = d.ip_address;
                }
            } catch (e) { /* silent */ }
        },

        startSimStatusPolling() {
            this.fetchSimStatus();
            if (!this.simStatusTimer) this.simStatusTimer = setInterval(() => this.fetchSimStatus(), 5000);
        },

        maskToken(token) {
            if (!token || token.length <= 12) return token;
            return token.substring(0, 6) + '••••••••' + token.substring(token.length - 6);
        },

        async copyToClipboard(text) {
            try {
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(text);
                    this.showToast('已复制到剪贴板');
                    return;
                }
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed'; ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                this.showToast('已复制到剪贴板');
            } catch (e) {
                this.showToast('复制失败', 'error');
            }
        },

        async saveStbConfig() {
            this.savingStb = true;
            try {
                const r = await fetch('/api/stb-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.stbConfig)
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message, res.status === 'warning' ? 'error' : 'success');
                    await this.fetchStbConfig();
                } else {
                    this.showToast(res.message || '保存失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            } finally { this.savingStb = false; }
        },

        // ---- Sync ----
        async triggerSync() {
            try {
                const r = await fetch('/api/sync/start', { method: 'POST' });
                const res = await r.json();
                if (res.status === 'started') {
                    this.showToast('同步已启动');
                    this.startSyncStatusPolling();
                } else if (res.status === 'already_running') {
                    this.showToast(res.message, 'error');
                    this.startSyncStatusPolling();
                } else {
                    this.showToast(res.message || '启动失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            }
        },

        async fetchSyncStatus() {
            try {
                const r = await fetch('/api/sync/status');
                this.syncStatus = await r.json();
                if (!this.syncStatus.running && this.syncStatusTimer) {
                    clearInterval(this.syncStatusTimer);
                    this.syncStatusTimer = null;
                    if (this.syncStatus.last_sync_time) {
                        this.showToast('同步完成!');
                        this.fetchDbStats();
                    }
                }
            } catch (e) { /* silent */ }
        },

        async fetchDbStats() {
            try {
                const r = await fetch('/api/sync/stats');
                this.dbStats = await r.json();
            } catch (e) { /* silent */ }
        },

        startSyncStatusPolling() {
            this.fetchSyncStatus();
            this.fetchDbStats();
            if (!this.syncStatusTimer) this.syncStatusTimer = setInterval(() => this.fetchSyncStatus(), 2000);
        },

        formatTime(ts) {
            if (!ts) return '—';
            return new Date(ts * 1000).toLocaleString('zh-CN');
        },

        // ---- Log ----
        async fetchLogs() {
            try {
                const r = await fetch(`/api/logs?lines=200&level=${this.logLevelFilter}`);
                const raw = await r.json();
                this.logs = raw.map(line => {
                    const m = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)$/);
                    if (m) return { time: m[1], level: m[2], message: m[3] };
                    return { time: '', level: 'INFO', message: line };
                });
                if (this.logAutoScroll) {
                    this.$nextTick(() => {
                        const c = this.$refs.logContainer;
                        if (c) c.scrollTop = c.scrollHeight;
                    });
                }
            } catch (e) { /* silent */ }
        },

        async clearLogs() {
            try {
                await fetch('/api/logs/clear', { method: 'POST' });
                this.logs = [];
                this.showToast('日志已清空');
            } catch (e) {
                this.showToast('清空失败', 'error');
            }
        },

        startLogPolling() {
            this.fetchLogs();
            if (!this.logPollTimer) this.logPollTimer = setInterval(() => this.fetchLogs(), 2000);
        }
    }
});

app.mount('#app');
