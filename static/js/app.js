/**
 * IPTV-Toolkit Web UI — Root Shell
 * 职责：路由切换、全局共享状态(provide)、组件注册
 */
const { createApp } = Vue;

const app = createApp({
    provide() {
        return {
            showToast: this.showToast,
            simStatus: this.simStatus,
            syncingLive: this.syncingLive,
            triggerSync: this.triggerSync,
            triggerEpgSync: this.triggerEpgSync,
            triggerLiveSync: this.triggerLiveSync,
            formatTime: this.formatTime,
        };
    },
    data() {
        return {
            activeTab: (() => { const t = localStorage.getItem('active_tab'); return t === 'sync' ? 'vod' : t || 'stb'; })(),
            theme: 'dark',

            tabTitles: {
                stb: '系统配置', vod: 'VOD 点播管理',
                live: '直播频道管理', epg: 'EPG 节目管理'
            },
            tabSubtitles: {
                stb: '管理机顶盒仿真认证凭证、定时同步任务与系统日志',
                vod: '从 VIS API 同步点播数据到本地 SQLite 数据库',
                live: '管理直播频道、分类、外部导入，生成 M3U 订阅',
                epg: '从 VIS 节目单 API 同步 EPG 数据，生成 XMLTV'
            },

            // Global shared state
            simStatus: { is_authenticated: false, epg_base_url: null, user_token: null, jsessionid: null },
            simStatusTimer: null,
            syncingLive: false,
        };
    },

    computed: {
        vodApiLinks() {
            const origin = window.location.origin;
            return { tvbox: `${origin}/zjvod`, api: `${origin}/api/vod` };
        },
        liveM3uUrl() {
            return `${window.location.origin}/tv.m3u`;
        },
        epgXmlLink() {
            return `${window.location.origin}/epg.xml`;
        },
    },

    watch: {
        activeTab(newTab) {
            localStorage.setItem('active_tab', newTab);
            this.stopAllPolling();
            this.fetchSimStatus();
            if (newTab === 'stb') this.startSimStatusPolling();
        },
    },

    created() {
        this.initTheme();
        this.fetchSimStatus();
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
            const toast = this.$refs.toastRef;
            if (toast) toast.show(msg, type);
        },

        // ---- Global polling (simStatus shared by StbTab + LiveTab) ----
        stopAllPolling() {
            if (this.simStatusTimer) { clearInterval(this.simStatusTimer); this.simStatusTimer = null; }
        },
        async fetchSimStatus() {
            try { this.simStatus = await stbService.getSimStatus(); } catch (e) {}
        },
        startSimStatusPolling() {
            this.fetchSimStatus();
            if (!this.simStatusTimer) this.simStatusTimer = setInterval(() => this.fetchSimStatus(), 5000);
        },

        // ---- Sync wrappers (injected by StbTab for manual sync buttons) ----
        async triggerSync() {
            try { const res = await vodService.triggerSync(); this.showToast(res.message || '同步已启动'); }
            catch (e) { this.showToast(e.message || '通信异常', 'error'); }
        },
        async triggerEpgSync() {
            try { const res = await epgService.triggerSync(); this.showToast(res.message || 'EPG 同步已启动'); }
            catch (e) { this.showToast(e.message || '通信异常', 'error'); }
        },
        async triggerLiveSync() {
            this.syncingLive = true;
            try { const res = await liveService.triggerSync(); this.showToast(res.message); }
            catch (e) { this.showToast(e.message || '网络请求异常', 'error'); }
            finally { this.syncingLive = false; }
        },

        formatTime(ts) {
            if (!ts) return '—';
            return new Date(ts * 1000).toLocaleString('zh-CN');
        },
    }
});

// 注册所有组件
app.component('toast-notification', ToastNotification);
app.component('color-picker', ColorPicker);
app.component('sidebar-nav', SidebarNav);
app.component('stb-tab', StbTab);
app.component('vod-tab', VodTab);
app.component('epg-tab', EpgTab);
app.component('live-tab', LiveTab);

app.mount('#app');
