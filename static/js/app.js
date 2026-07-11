/**
 * IPTV-Toolkit v2.0 前端逻辑
 * - 系统配置
 * - 数据同步管理
 * - 系统日志查看
 */
const { createApp } = Vue;

const app = createApp({
    data() {
        return {
            activeTab: localStorage.getItem('active_tab') || 'stb',
            theme: 'dark',

            // Toast
            toast: { show: false, message: '', type: 'success', timeoutId: null },

            // Tab info
            tabTitles: {
                stb: '系统配置',
                sync: '数据同步管理',
                live: '直播频道管理',
                epg: 'EPG 节目管理',
                log: '系统日志'
            },
            tabSubtitles: {
                stb: '管理机顶盒仿真认证凭证与定时同步任务',
                sync: '从 VIS API 同步点播数据到本地 SQLite 数据库',
                live: '管理直播频道、分类、外部导入，生成 M3U 订阅',
                epg: '从 VIS 节目单 API 同步 EPG 数据，生成 XMLTV',
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

            // Plate 1: 定时同步设置
            schedulerConfig: { live_sync_hour: 0, vod_sync_hour: 1, epg_sync_hour: 1 },
            schedulerStatus: { running: false, config: {}, tasks: {} },
            savingScheduler: false,
            taskLabels: { live: '直播频道', vod: 'VOD 点播', epg: 'EPG 节目单' },

            // Plate 2: Sync
            syncStatus: {
                running: false, progress: '', current_type: '',
                done: 0, total: 0, last_sync_time: null, last_error: null
            },
            dbStats: { total: 0, types: {}, last_synced: 0 },
            syncStatusTimer: null,

            // Plate 4: Live Channel Management
            liveFilter: {
                category_id: null, enabled: null, source: null, keyword: '', page: 1, limit: 10000
            },
            liveChannels: [],
            liveTotal: 0,
            liveCategories: [],
            liveConfig: {
                udpxy_address: '', epg_url: '', logo_base_url: '',
                fcc_global_enabled_bool: false, timeshift_enabled_bool: false,
                m3u_dual_line_bool: false,
                low_quality_filter_bool: true, m3u8_filter_bool: true
            },
            // VOD 过滤设置（独立于 liveConfig，避免保存时覆盖直播配置）
            vodConfig: {
                low_quality_filter_bool: true,
                m3u8_filter_bool: true
            },
            newCategory: { name: '', sort_index: 0, color: '#6366f1', is_visible: 1 },
            editingCh: null,
            showAliasModal: false,
            aliases: [],
            newAlias: { source_name: '', target_name: '' },
            showLiveConfigModal: false,
            showCategoryModal: false,
            categorySortableInstance: null,
            categoryTbodyKey: 0,
            categoryImportCleanMode: false,
            showEditChannelModal: false,
            syncingLive: false,
            presetColors: ['#6366f1', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#ef4444', '#8b5cf6', '#14b8a6', '#f97316', '#06b6d4', '#84cc16', '#e11d48'],
            importFormat: 'm3u',
            importMethod: 'text',
            importText: '',
            importFile: null,
            importingChannels: false,
            liveStats: { server: 0, external: 0, enabled: 0, disabled: 0 },
            sortableInstance: null,
            selectedChannelIds: [],
            selectAllChannels: false,
            tbodyKey: 0,

            // Plate 5: EPG
            epgSyncStatus: { running: false, progress: '', last_sync_time: null },
            epgSyncTimer: null,
            epgStats: { total_programs: 0, total_channels: 0, date_range: null },
            nowPlaying: [], nowPlayingLoaded: false,

            // EPG Preview Modal
            showEpgPreviewModal: false,
            epgPreviewChannel: null,
            epgPreviewDates: [],
            epgPreviewDateIndex: 0,
            epgLoading: false,
            epgPrograms: [],

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
        },
        liveM3uUrl() {
            const origin = window.location.origin;
            return `${origin}/tv.m3u`;
        },
        epgXmlLink() {
            return `${window.location.origin}/epg.xml`;
        },
        canBatchDelete() {
            if (this.selectedChannelIds.length === 0) return false;
            return this.selectedChannelIds.every(id => {
                const ch = this.liveChannels.find(c => c.id === id);
                return ch && ch.source === 'external';
            });
        },
        epgFormattedDate() {
            if (this.epgPreviewDates.length === 0 || this.epgPreviewDateIndex < 0 || this.epgPreviewDateIndex >= this.epgPreviewDates.length) return '';
            const dateStr = this.epgPreviewDates[this.epgPreviewDateIndex];
            return this.formatEpgDateLabel(dateStr);
        }
    },

    watch: {
        activeTab(newTab) {
            localStorage.setItem('active_tab', newTab);
            this.stopAllPolling();
            this.fetchSimStatus(); // Refresh auth status on tab switch
            if (newTab === 'stb') {
                this.startSimStatusPolling();
                this.fetchSchedulerConfig();
                this.fetchSchedulerStatus();
            } else if (newTab === 'log') {
                this.startLogPolling();
            } else if (newTab === 'sync') {
                this.startSyncStatusPolling();
            } else if (newTab === 'live') {
                this.initLiveTab();
            } else if (newTab === 'epg') {
                this.fetchEpgStats();
                this.startEpgSyncPolling();
            }
        },
        selectedChannelIds(newVal) {
            if (newVal.length === 0) {
                this.selectAllChannels = false;
            } else if (this.liveChannels.length > 0 && newVal.length === this.liveChannels.length) {
                this.selectAllChannels = true;
            } else {
                this.selectAllChannels = false;
            }
        },
        'liveConfig.udpxy_enabled_bool'(newVal) {
            if (this.loadingConfig) return;
            if (newVal) {
                this.liveConfig.fcc_global_enabled_bool = true;
            } else {
                this.liveConfig.fcc_global_enabled_bool = false;
            }
        }
    },

    created() {
        this.initTheme();
        this.fetchStbConfig();
        this.fetchDbStats();
        this.fetchSimStatus(); // Initial fetch of auth status globally
        this.fetchVodConfig();  // 加载 VOD 过滤设置（独立于 liveConfig）
        this.fetchSchedulerConfig();  // 加载定时同步钟点配置
        this.fetchSchedulerStatus();  // 加载调度器运行状态
        if (this.activeTab === 'stb') {
            this.startSimStatusPolling();
        } else if (this.activeTab === 'live') {
            this.initLiveTab();
        } else if (this.activeTab === 'log') {
            this.startLogPolling();
        } else if (this.activeTab === 'sync') {
            this.startSyncStatusPolling();
        }
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

        // ---- EPG Preview Modal Methods ----
        openEpgPreview(ch) {
            this.epgPreviewChannel = ch;
            this.showEpgPreviewModal = true;
            this.epgPrograms = [];
            
            // Calculate selectable dates: always show full range of past 7 days to tomorrow
            const dates = [];
            const backTime = 7;
            const now = new Date();
            
            // Get dates list
            for (let i = -backTime; i <= 1; i++) {
                const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
                const year = d.getFullYear();
                const month = String(d.getMonth() + 1).padStart(2, '0');
                const day = String(d.getDate()).padStart(2, '0');
                dates.push(`${year}-${month}-${day}`);
            }
            this.epgPreviewDates = dates;
            // Default select today (index is backTime)
            this.epgPreviewDateIndex = backTime;
            
            this.fetchEpgPrograms();
        },

        async fetchEpgPrograms() {
            if (!this.epgPreviewChannel) return;
            this.epgLoading = true;
            this.epgPrograms = [];
            const dateStr = this.epgPreviewDates[this.epgPreviewDateIndex];
            const channelId = this.epgPreviewChannel.tvg_id || this.epgPreviewChannel.channel_id || this.epgPreviewChannel.name;
            
            try {
                const url = `/api/epg/programs?channel_id=${encodeURIComponent(channelId)}&date=${dateStr}&limit=200`;
                const r = await fetch(url);
                if (r.ok) {
                    const data = await r.json();
                    this.epgPrograms = data.items || [];
                    
                    // Auto scroll to playing program after DOM updates
                    this.$nextTick(() => {
                        this.scrollToPlayingProgram();
                    });
                }
            } catch (e) {
                console.error("加载节目单失败:", e);
            } finally {
                this.epgLoading = false;
            }
        },

        prevEpgDay() {
            if (this.epgPreviewDateIndex > 0) {
                this.epgPreviewDateIndex--;
                this.fetchEpgPrograms();
            }
        },

        nextEpgDay() {
            if (this.epgPreviewDateIndex < this.epgPreviewDates.length - 1) {
                this.epgPreviewDateIndex++;
                this.fetchEpgPrograms();
            }
        },

        isProgramPlaying(prog) {
            if (!prog.start_time || !prog.end_time) return false;
            try {
                // Parse "YYYY-MM-DD HH:MM:SS" manually to avoid browser TZ/DST discrepancies
                const parseDate = (str) => {
                    const parts = str.split(' ');
                    const ymd = parts[0].split('-');
                    const hms = parts[1].split(':');
                    return new Date(ymd[0], ymd[1] - 1, ymd[2], hms[0], hms[1], hms[2]);
                };
                const start = parseDate(prog.start_time);
                const end = parseDate(prog.end_time);
                const now = new Date();
                return now >= start && now <= end;
            } catch (e) {
                return false;
            }
        },

        formatProgTimeRange(prog) {
            if (!prog.start_time || !prog.end_time) return '';
            try {
                const getHM = (str) => {
                    const timePart = str.split(' ')[1];
                    const parts = timePart.split(':');
                    return `${parts[0]}:${parts[1]}`;
                };
                return `${getHM(prog.start_time)} - ${getHM(prog.end_time)}`;
            } catch (e) {
                return '';
            }
        },

        formatEpgDateLabel(dateStr) {
            if (!dateStr) return '';
            try {
                const parts = dateStr.split('-');
                const targetDate = new Date(parts[0], parts[1] - 1, parts[2]);
                
                const now = new Date();
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                const diffTime = targetDate - today;
                const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));
                
                let rel = '';
                if (diffDays === 0) rel = '今天';
                else if (diffDays === -1) rel = '昨天';
                else if (diffDays === 1) rel = '明天';
                else {
                    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
                    rel = weekdays[targetDate.getDay()];
                }
                return `${parts[1]}月${parts[2]}日 (${rel})`;
            } catch (e) {
                return dateStr;
            }
        },

        scrollToPlayingProgram() {
            setTimeout(() => {
                const container = this.$refs.epgProgramList;
                const rows = this.$refs.epgRows;
                if (container && rows && rows.length > 0) {
                    // Find the DOM element that corresponds to the currently playing program
                    const playingEl = rows.find((el, index) => {
                        const prog = this.epgPrograms[index];
                        return prog && this.isProgramPlaying(prog);
                    });
                    if (playingEl) {
                        const containerHeight = container.clientHeight;
                        const containerRect = container.getBoundingClientRect();
                        const elRect = playingEl.getBoundingClientRect();
                        
                        // Calculate exact offset relative to container's client area
                        const elTop = elRect.top - containerRect.top + container.scrollTop;
                        const elHeight = elRect.height || playingEl.clientHeight;
                        
                        // Center the element
                        container.scrollTop = elTop - (containerHeight / 2) + (elHeight / 2);
                    }
                }
            }, 150);
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

        // ---- 定时同步设置（钟点存于 live_config，状态来自调度器）----
        async fetchSchedulerConfig() {
            try {
                const r = await fetch('/api/live/config');
                const c = await r.json();
                this.schedulerConfig = {
                    live_sync_hour: parseInt(c.live_sync_hour ?? 0) || 0,
                    vod_sync_hour: parseInt(c.vod_sync_hour ?? 1) || 0,
                    epg_sync_hour: parseInt(c.epg_sync_hour ?? 1) || 0
                };
            } catch (e) { /* silent */ }
        },

        async fetchSchedulerStatus() {
            try {
                const r = await fetch('/api/scheduler/status');
                if (r.ok) this.schedulerStatus = await r.json();
            } catch (e) { /* silent */ }
        },

        async saveSchedulerConfig() {
            this.savingScheduler = true;
            try {
                const r = await fetch('/api/live/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        live_sync_hour: String(this.schedulerConfig.live_sync_hour),
                        vod_sync_hour: String(this.schedulerConfig.vod_sync_hour),
                        epg_sync_hour: String(this.schedulerConfig.epg_sync_hour)
                    })
                });
                if (r.ok) {
                    this.showToast('定时设置已保存，即时生效');
                    this.fetchSchedulerStatus();
                } else {
                    this.showToast('保存失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            } finally { this.savingScheduler = false; }
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

        // ---- EPG ----
        async triggerEpgSync() {
            try { const r = await fetch('/api/epg/sync', { method: 'POST' }); const res = await r.json();
                if (res.status === 'started') { this.showToast('EPG 同步已启动'); this.startEpgSyncPolling(); }
                else if (res.status === 'already_running') { this.showToast(res.message, 'error'); this.startEpgSyncPolling(); }
                else this.showToast(res.message || '启动失败', 'error');
            } catch (e) { this.showToast('通信异常', 'error'); }
        },
        async fetchEpgSyncStatus() {
            try { const r = await fetch('/api/epg/sync/status'); this.epgSyncStatus = await r.json();
                if (!this.epgSyncStatus.running && this.epgSyncTimer) { clearInterval(this.epgSyncTimer); this.epgSyncTimer = null; if (this.epgSyncStatus.last_sync_time) { this.showToast('EPG 同步完成!'); this.fetchEpgStats(); } }
            } catch (e) {}
        },
        async fetchEpgStats() {
            try { const r = await fetch('/api/epg/stats'); this.epgStats = await r.json(); } catch (e) {}
        },
        startEpgSyncPolling() {
            this.fetchEpgSyncStatus(); this.fetchEpgStats();
            if (!this.epgSyncTimer) this.epgSyncTimer = setInterval(() => this.fetchEpgSyncStatus(), 2000);
        },
        async fetchNowPlaying() {
            try { const r = await fetch('/api/epg/programs/now'); const data = await r.json(); this.nowPlaying = data.items || []; this.nowPlayingLoaded = true; } catch (e) {}
        },
        copyEpgXmlLink() { this.copyToClipboard(this.epgXmlLink); },

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
        },

        // ---- Live Channel Management ----
        initLiveTab() {
            this.fetchLiveCategories();
            this.fetchLiveConfig();
            this.fetchLiveChannels(1);
            this.fetchLiveStats();
        },

        async fetchLiveChannels(page = 1) {
            this.liveFilter.page = page;
            try {
                const params = new URLSearchParams();
                if (this.liveFilter.category_id !== null) params.append('category_id', this.liveFilter.category_id);
                if (this.liveFilter.enabled !== null) params.append('enabled', this.liveFilter.enabled);
                if (this.liveFilter.source !== null) params.append('source', this.liveFilter.source);
                if (this.liveFilter.keyword) params.append('keyword', this.liveFilter.keyword);
                params.append('page', this.liveFilter.page);
                params.append('limit', this.liveFilter.limit);

                const r = await fetch(`/api/live/channels?${params.toString()}`);
                const data = await r.json();
                this.liveChannels = data.channels;
                this.liveTotal = data.total;
                this.tbodyKey++;
                this.selectedChannelIds = [];
                this.selectAllChannels = false;
                
                this.$nextTick(() => {
                    this.initSortable();
                });
            } catch (e) {
                this.showToast('加载直播频道失败', 'error');
            }
        },

        async fetchLiveCategories() {
            try {
                const r = await fetch('/api/live/categories');
                this.liveCategories = await r.json();
            } catch (e) { /* silent */ }
        },

        async fetchLiveConfig() {
            this.loadingConfig = true;
            try {
                const r = await fetch('/api/live/config');
                const config = await r.json();
                this.liveConfig = {
                    ...config,
                    udpxy_enabled_bool: config.udpxy_enabled === '1',
                    fcc_global_enabled_bool: config.fcc_global_enabled === '1',
                    timeshift_enabled_bool: config.timeshift_enabled === '1',
                    m3u_dual_line_bool: config.m3u_dual_line === '1',
                    low_quality_filter_bool: config.low_quality_filter !== '0',  // 默认开启
                    m3u8_filter_bool: config.m3u8_filter !== '0'  // 默认开启
                };
            } catch (e) { /* silent */ }
            this.$nextTick(() => {
                this.loadingConfig = false;
            });
        },

        async fetchLiveStats() {
            try {
                const r = await fetch('/api/live/stats');
                this.liveStats = await r.json();
            } catch (e) { /* silent */ }
        },

        getLogoUrl(logo) {
            if (!logo) return '';
            if (logo.startsWith('http://') || logo.startsWith('https://')) {
                return logo;
            }
            const base = this.liveConfig.logo_base_url || '/static/logo/';
            const cleanBase = base.endsWith('/') ? base : base + '/';
            return cleanBase + logo;
        },

        handleLogoError(ch) {
            if (ch.logo_fallback_tried) {
                ch.logo_failed = true;
                return;
            }
            ch.logo_fallback_tried = true;
            const logo = ch.logo_url;
            if (logo) {
                const lower = logo.toLowerCase();
                if (lower.includes('4k') || lower.includes('8k')) {
                    const cleanLogo = logo.replace(/(?:\s*|-|_)?(?:4[kK]|8[kK])/g, '');
                    if (cleanLogo && cleanLogo !== logo) {
                        ch.logo_url = cleanLogo;
                        return;
                    }
                }
            }
            ch.logo_failed = true;
        },

        async triggerLiveSync() {
            this.syncingLive = true;
            try {
                const r = await fetch('/api/live/sync', { method: 'POST' });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message);
                    this.initLiveTab();
                } else {
                    this.showToast(res.message || '同步失败', 'error');
                }
            } catch (e) {
                this.showToast('网络请求异常', 'error');
            } finally {
                this.syncingLive = false;
            }
        },

        async toggleChannelEnabled(ch) {
            const next_enabled = ch.is_enabled === 1 ? 0 : 1;
            try {
                const r = await fetch(`/api/live/channels/${ch.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ...ch,
                        is_enabled: next_enabled
                    })
                });
                if (r.ok) {
                    ch.is_enabled = next_enabled;
                    this.showToast(ch.is_enabled ? '频道已启用' : '频道已禁用');
                    this.fetchLiveStats();
                } else {
                    this.showToast('操作失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        editChannel(ch) {
            this.editingCh = { ...ch };
            this.showEditChannelModal = true;
        },

        async saveChannelEdit() {
            if (!this.editingCh) return;
            try {
                const r = await fetch(`/api/live/channels/${this.editingCh.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.editingCh)
                });
                if (r.ok) {
                    this.showToast('频道修改成功');
                    this.showEditChannelModal = false;
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '修改失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        async changeChannelCategory(ch) {
            try {
                const r = await fetch(`/api/live/channels/${ch.id}`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(ch)
                });
                if (r.ok) {
                    this.showToast('分类已更新');
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '更新失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        async deleteChannel(id) {
            if (!confirm('确定要删除这个外部频道吗？')) return;
            try {
                const r = await fetch(`/api/live/channels/${id}`, { method: 'DELETE' });
                if (r.ok) {
                    this.showToast('频道删除成功');
                    this.fetchLiveChannels(this.liveFilter.page);
                    this.fetchLiveStats();
                } else {
                    this.showToast('删除失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        openLiveConfigModal() {
            this.showLiveConfigModal = true;
        },

        async saveLiveConfig() {
            const payload = {
                ...this.liveConfig,
                udpxy_enabled: this.liveConfig.udpxy_enabled_bool ? '1' : '0',
                fcc_global_enabled: this.liveConfig.fcc_global_enabled_bool ? '1' : '0',
                timeshift_enabled: this.liveConfig.timeshift_enabled_bool ? '1' : '0',
                m3u_dual_line: this.liveConfig.m3u_dual_line_bool ? '1' : '0',
                low_quality_filter: this.liveConfig.low_quality_filter_bool ? '1' : '0',
                m3u8_filter: this.liveConfig.m3u8_filter_bool ? '1' : '0'
            };
            delete payload.udpxy_enabled_bool;
            delete payload.fcc_global_enabled_bool;
            delete payload.timeshift_enabled_bool;
            delete payload.m3u_dual_line_bool;
            delete payload.low_quality_filter_bool;
            delete payload.m3u8_filter_bool;

            try {
                const r = await fetch('/api/live/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (r.ok) {
                    this.showToast('直播配置保存成功');
                    this.showLiveConfigModal = false;
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    this.showToast('配置保存失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        // VOD 过滤设置（独立于 liveConfig，仅发送过滤字段，不影响直播配置）
        async fetchVodConfig() {
            try {
                const r = await fetch('/api/live/config');
                const config = await r.json();
                this.vodConfig.low_quality_filter_bool = config.low_quality_filter !== '0';
                this.vodConfig.m3u8_filter_bool = config.m3u8_filter !== '0';
            } catch (e) {
                console.warn('获取 VOD 过滤设置失败，使用默认值', e);
            }
        },

        async saveVodConfig() {
            const payload = {
                low_quality_filter: this.vodConfig.low_quality_filter_bool ? '1' : '0',
                m3u8_filter: this.vodConfig.m3u8_filter_bool ? '1' : '0'
            };
            try {
                const r = await fetch('/api/live/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (r.ok) {
                    this.showToast('过滤设置已保存');
                } else {
                    this.showToast('保存失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        openCategoryModal() {
            this.showCategoryModal = true;
            const nextIndex = this.liveCategories.length > 0
                ? Math.max(...this.liveCategories.map(c => c.sort_index)) + 1
                : 0;
            this.newCategory = {
                name: '', sort_index: nextIndex,
                color: this.presetColors[Math.floor(Math.random() * this.presetColors.length)],
                is_visible: 1, showColorPicker: false
            };
            // 使用 setTimeout 确保弹窗 DOM 完全渲染后再初始化 Sortable
            setTimeout(() => { this.initCategorySortable(); }, 150);
        },

        initCategorySortable() {
            const el = document.getElementById('live-category-list-tbody');
            if (!el) return;
            if (this.categorySortableInstance) { this.categorySortableInstance.destroy(); }
            if (window.Sortable) {
                this.categorySortableInstance = window.Sortable.create(el, {
                    handle: '.drag-handle',
                    animation: 150,
                    onEnd: async (evt) => {
                        const draggedId = parseInt(evt.item.getAttribute('data-id'));
                        const nextEl = evt.item.nextElementSibling;
                        let targetSiblingId = null;
                        if (nextEl) { targetSiblingId = parseInt(nextEl.getAttribute('data-id')); }
                        const draggedItems = this.liveCategories.filter(c => c.id === draggedId);
                        const remainingItems = this.liveCategories.filter(c => c.id !== draggedId);
                        let insertIdx = remainingItems.length;
                        if (targetSiblingId !== null) {
                            const idx = remainingItems.findIndex(c => c.id === targetSiblingId);
                            if (idx !== -1) insertIdx = idx;
                        }
                        remainingItems.splice(insertIdx, 0, ...draggedItems);
                        // 更新每个 item 的 sort_index，使界面即时反映新序号
                        remainingItems.forEach((item, index) => { item.sort_index = index; });
                        const order = remainingItems.map((item, index) => ({ id: item.id, sort_index: index }));
                        this.liveCategories = remainingItems;
                        this.categoryTbodyKey++;
                        this.$nextTick(() => { this.initCategorySortable(); });
                        try {
                            const r = await fetch('/api/live/categories/reorder', {
                                method: 'POST', headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ order })
                            });
                            if (r.ok) {
                                this.showToast('分类排序已更新');
                                // 从服务器重新加载以保持数据一致
                                await this.fetchLiveCategories();
                                this.categoryTbodyKey++;
                                this.$nextTick(() => { this.initCategorySortable(); });
                            }
                            else { this.showToast('保存排序失败', 'error'); }
                        } catch(e) { this.showToast('排序请求失败', 'error'); }
                    }
                });
            }
        },

        async addLiveCategory() {
            if (!this.newCategory.name.trim()) {
                this.showToast('请输入分类名称', 'error');
                return;
            }
            try {
                const r = await fetch('/api/live/categories', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.newCategory)
                });
                if (r.ok) {
                    this.showToast('分类添加成功');
                    await this.fetchLiveCategories();
                    const nextIdx = this.liveCategories.length > 0
                        ? Math.max(...this.liveCategories.map(c => c.sort_index)) + 1
                        : 0;
                    this.newCategory = { name: '', sort_index: nextIdx, color: '#6366f1', is_visible: 1, showColorPicker: false };
                    this.categoryTbodyKey++;
                    this.$nextTick(() => { this.initCategorySortable(); });
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '添加失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        async updateLiveCategory(cat) {
            try {
                const r = await fetch(`/api/live/categories/${cat.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(cat)
                });
                if (r.ok) {
                    this.showToast('分类修改成功');
                    await this.fetchLiveCategories();
                    this.fetchLiveChannels(this.liveFilter.page);
                    this.categoryTbodyKey++;
                    this.$nextTick(() => { this.initCategorySortable(); });
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '修改失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        async deleteLiveCategory(id) {
            if (!confirm('确定要删除此分类吗？关联的频道将自动归入"未分类"。')) return;
            try {
                const r = await fetch(`/api/live/categories/${id}`, { method: 'DELETE' });
                if (r.ok) {
                    this.showToast('分类删除成功');
                    await this.fetchLiveCategories();
                    this.fetchLiveChannels(this.liveFilter.page);
                    this.categoryTbodyKey++;
                    this.$nextTick(() => { this.initCategorySortable(); });
                } else {
                    this.showToast('删除失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        handleImportFileSelect(e) {
            const files = e.target.files;
            if (files && files.length > 0) {
                this.importFile = files[0];
            }
        },

        handleImportFileDrop(e) {
            const files = e.dataTransfer.files;
            if (files && files.length > 0) {
                const file = files[0];
                const ext = file.name.toLowerCase().split('.').pop();
                if (ext === 'm3u' || ext === 'm3u8') {
                    this.importFile = file;
                } else {
                    this.showToast('只支持导入 M3U 播放列表文件 (*.m3u / *.m3u8)', 'error');
                }
            }
        },

        async importExternalChannels() {
            this.importingChannels = true;
            try {
                if (this.importMethod === 'file') {
                    if (!this.importFile) {
                        this.showToast('请先选择要上传的文件', 'error');
                        return;
                    }
                    const formData = new FormData();
                    formData.append('file', this.importFile);
                    const r = await fetch('/api/live/import', {
                        method: 'POST',
                        body: formData
                    });
                    const res = await r.json();
                    if (r.ok) {
                        this.showToast(`导入成功！新增 ${res.new} 个，跳过 ${res.skipped} 个，总共 ${res.total} 个`);
                        this.importFile = null;
                        this.initLiveTab();
                    } else {
                        this.showToast(res.detail || res.message || '导入失败', 'error');
                    }
                } else {
                    if (!this.importText.trim()) {
                        this.showToast('请粘贴文本内容再进行导入', 'error');
                        return;
                    }
                    const r = await fetch('/api/live/import', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            content: this.importText
                        })
                    });
                    const res = await r.json();
                    if (r.ok) {
                        this.showToast(`导入成功！新增 ${res.new} 个，跳过 ${res.skipped} 个，总共 ${res.total} 个`);
                        this.importText = '';
                        this.initLiveTab();
                    } else {
                        this.showToast(res.detail || res.message || '导入失败', 'error');
                    }
                }
            } catch (e) {
                this.showToast('导入过程中遭遇异常', 'error');
            } finally {
                this.importingChannels = false;
            }
        },

        openAliasModal() {
            this.showAliasModal = true;
            this.fetchAliases();
        },
        quickAddAlias(sourceName) {
            this.newAlias.source_name = sourceName;
            this.newAlias.target_name = '';
            this.showAliasModal = true;
            this.fetchAliases();
            this.$nextTick(() => {
                const inputs = document.querySelectorAll('.category-add-inline input[type=text]');
                if (inputs.length >= 2) inputs[1].focus();
            });
        },
        async fetchAliases() {
            try {
                const r = await fetch('/api/live/aliases');
                this.aliases = await r.json();
            } catch(e) {}
        },
        async addAlias() {
            if (!this.newAlias.source_name.trim() || !this.newAlias.target_name.trim()) {
                this.showToast('原始名称和规范名称均不能为空', 'error'); return;
            }
            try {
                const r = await fetch('/api/live/aliases', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.newAlias)
                });
                if (r.ok) {
                    const res = await r.json();
                    this.showToast('别名添加成功' + (res.affected_channels ? '，已更新 ' + res.affected_channels + ' 个频道' : ''));
                    this.newAlias = { source_name: '', target_name: '' };
                    this.fetchAliases();
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '添加失败', 'error');
                }
            } catch(e) { this.showToast('网络错误', 'error'); }
        },
        async saveAlias(a) {
            try {
                const r = await fetch(`/api/live/aliases/${a.id}`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source_name: a.source_name, target_name: a.target_name })
                });
                if (r.ok) {
                    const res = await r.json();
                    this.showToast('别名保存成功' + (res.affected_channels ? '，已更新 ' + res.affected_channels + ' 个频道' : ''));
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '保存失败', 'error');
                }
            } catch(e) { this.showToast('网络错误', 'error'); }
        },
        async deleteAlias(id) {
            if (!confirm('确定要删除这个别名映射吗？相关频道将恢复原始名称。')) return;
            try {
                const r = await fetch(`/api/live/aliases/${id}`, { method: 'DELETE' });
                if (r.ok) {
                    this.showToast('别名删除成功，频道已恢复原始名称');
                    this.fetchAliases();
                    this.fetchLiveChannels(this.liveFilter.page);
                } else { this.showToast('删除失败', 'error'); }
            } catch(e) { this.showToast('网络错误', 'error'); }
        },
        async reapplyAliases() {
            try {
                const r = await fetch('/api/live/aliases/reapply', { method: 'POST' });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(`已重新应用：${res.applied} 条映射，${res.affected} 个频道`);
                    this.fetchLiveChannels(this.liveFilter.page);
                } else { this.showToast(res.detail || '应用失败', 'error'); }
            } catch(e) { this.showToast('网络错误', 'error'); }
        },
        async exportAliases() {
            try {
                const r = await fetch('/api/live/aliases/export');
                const data = await r.json();
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `live_aliases_${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                this.showToast(`已导出 ${data.count} 条别名映射`);
            } catch(e) { this.showToast('导出失败', 'error'); }
        },
        triggerAliasImport() { this.$refs.aliasFileInput.click(); },
        async handleAliasFileImport(e) {
            const file = e.target.files[0];
            if (!file) return;
            try {
                const text = await file.text();
                const data = JSON.parse(text);
                const r = await fetch('/api/live/aliases/import', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(`导入成功，共 ${res.imported} 条映射`);
                    this.fetchAliases();
                } else { this.showToast(res.detail || '导入失败', 'error'); }
            } catch(e) { this.showToast('文件解析失败，请检查 JSON 格式', 'error'); }
            e.target.value = '';
        },

        async exportCategoryMappings() {
            try {
                const r = await fetch('/api/live/categories/mappings/export');
                const data = await r.json();
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `live_category_mappings_${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                this.showToast(`已导出 ${data.count} 条频道-分类关系`);
            } catch(e) { this.showToast('导出关系失败', 'error'); }
        },
        triggerCategoryMappingImport() {
            this.$refs.categoryMappingFileInput.click();
        },
        async handleCategoryMappingFileImport(e) {
            const file = e.target.files[0];
            if (!file) return;
            try {
                const text = await file.text();
                const data = JSON.parse(text);
                const r = await fetch('/api/live/categories/mappings/import', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(`导入成功：关联了 ${res.imported_channels} 个频道的分类` + (res.created_categories ? `，自动创建了 ${res.created_categories} 个新分类` : ''));
                    this.fetchLiveCategories();
                    this.fetchLiveChannels(this.liveFilter.page);
                } else { this.showToast(res.detail || '导入失败', 'error'); }
            } catch(e) { this.showToast('文件解析失败，请检查 JSON 格式', 'error'); }
            e.target.value = '';
        },

        changeLivePage(page) {
            this.fetchLiveChannels(page);
        },

        toggleSelectAll() {
            if (this.selectAllChannels) {
                this.selectedChannelIds = this.liveChannels.map(c => c.id);
            } else {
                this.selectedChannelIds = [];
            }
        },

        async batchSetEnabled(enabled) {
            if (this.selectedChannelIds.length === 0) return;
            try {
                const r = await fetch('/api/live/channels/batch-enabled', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ids: this.selectedChannelIds,
                        enabled: enabled
                    })
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message);
                    this.selectedChannelIds = [];
                    this.fetchLiveChannels(this.liveFilter.page);
                    this.fetchLiveStats();
                } else {
                    this.showToast(res.detail || '批量设置状态失败', 'error');
                }
            } catch (e) {
                this.showToast('网络请求异常', 'error');
            }
        },

        async batchChangeCategory(event) {
            const catId = parseInt(event.target.value);
            if (isNaN(catId) || this.selectedChannelIds.length === 0) return;
            try {
                const r = await fetch('/api/live/channels/batch-category', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ids: this.selectedChannelIds,
                        category_id: catId
                    })
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message);
                    this.selectedChannelIds = [];
                    event.target.value = '';
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    this.showToast(res.detail || '批量修改分类失败', 'error');
                }
            } catch (e) {
                this.showToast('网络请求异常', 'error');
            }
        },

        async batchDelete() {
            if (this.selectedChannelIds.length === 0) return;
            const hasServer = this.selectedChannelIds.some(id => {
                const ch = this.liveChannels.find(c => c.id === id);
                return ch && ch.source === 'server';
            });
            if (hasServer) {
                this.showToast('服务器频道不能删除，请取消选中后重试', 'error');
                return;
            }
            if (!confirm(`确定要删除选中的 ${this.selectedChannelIds.length} 个频道吗？`)) return;
            try {
                const r = await fetch('/api/live/channels/batch-delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ids: this.selectedChannelIds
                    })
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message);
                    this.selectedChannelIds = [];
                    this.fetchLiveChannels(this.liveFilter.page);
                    this.fetchLiveStats();
                } else {
                    this.showToast(res.detail || '批量删除失败', 'error');
                }
            } catch (e) {
                this.showToast('网络请求异常', 'error');
            }
        },

        async sortChannelsByCategory() {
            // 按分类的排序索引排序，同分类内保持当前 sort_index
            const catOrderMap = {};
            this.liveCategories.forEach(cat => { catOrderMap[cat.id] = cat.sort_index || 0; });
            const sorted = [...this.liveChannels].sort((a, b) => {
                const orderA = catOrderMap[a.category_id] !== undefined ? catOrderMap[a.category_id] : Number.MAX_SAFE_INTEGER;
                const orderB = catOrderMap[b.category_id] !== undefined ? catOrderMap[b.category_id] : Number.MAX_SAFE_INTEGER;
                if (orderA !== orderB) return orderA - orderB;
                return (a.sort_index || 0) - (b.sort_index || 0);
            });
            const order = sorted.map((ch, i) => ({ id: ch.id, sort_index: i }));
            try {
                const r = await fetch('/api/live/channels/reorder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order })
                });
                if (r.ok) {
                    this.showToast('已按类别排序');
                    this.fetchLiveChannels(1);
                } else {
                    this.showToast('排序失败', 'error');
                }
            } catch (e) {
                this.showToast('网络异常', 'error');
            }
        },

        async resetLiveChannelsOrder() {
            if (!confirm('确定要恢复默认排序吗？这会清除拖拽自定义顺序，并按系统序号/ID恢复初始顺序。')) return;
            try {
                const r = await fetch('/api/live/channels/reset-order', { method: 'POST' });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message);
                    this.fetchLiveChannels(1);
                } else {
                    this.showToast('重置排序失败', 'error');
                }
            } catch (e) {
                this.showToast('网络连接异常', 'error');
            }
        },

        initSortable() {
            const el = document.getElementById('live-channel-list-tbody');
            if (!el) return;
            if (this.sortableInstance) {
                this.sortableInstance.destroy();
            }
            if (window.Sortable) {
                this.sortableInstance = window.Sortable.create(el, {
                    handle: '.drag-handle',
                    animation: 150,
                    onStart: (evt) => {
                        const draggedId = parseInt(evt.item.getAttribute('data-id'));
                        if (this.selectedChannelIds.includes(draggedId)) {
                            const nameValEl = evt.item.querySelector('.channel-name-val');
                            if (nameValEl) {
                                this.draggedOrigText = nameValEl.textContent;
                                nameValEl.textContent = `📦 正在拖拽 ${this.selectedChannelIds.length} 个已选频道...`;
                            }
                            this.selectedChannelIds.forEach(id => {
                                if (id !== draggedId) {
                                    const rowEl = el.querySelector(`.live-channel-row[data-id="${id}"]`);
                                    if (rowEl) {
                                        rowEl.classList.add('sortable-batch-ghost');
                                    }
                                }
                            });
                        }
                    },
                    onEnd: async (evt) => {
                        const draggedId = parseInt(evt.item.getAttribute('data-id'));
                        const isBatch = this.selectedChannelIds.includes(draggedId);
                        
                        // Clean up visual changes
                        const ghostRows = el.querySelectorAll('.sortable-batch-ghost');
                        ghostRows.forEach(row => row.classList.remove('sortable-batch-ghost'));
                        
                        if (isBatch && this.draggedOrigText) {
                            const nameValEl = evt.item.querySelector('.channel-name-val');
                            if (nameValEl) {
                                nameValEl.textContent = this.draggedOrigText;
                            }
                        }
                        
                        const dragIds = isBatch ? [...this.selectedChannelIds] : [draggedId];
                        
                        // Find where it was dropped relative to neighboring DOM elements
                        const nextEl = evt.item.nextElementSibling;
                        let targetSiblingId = null;
                        if (nextEl) {
                            targetSiblingId = parseInt(nextEl.getAttribute('data-id'));
                        }
                        
                        const draggedItems = this.liveChannels.filter(c => dragIds.includes(c.id));
                        const remainingItems = this.liveChannels.filter(c => !dragIds.includes(c.id));
                        
                        let insertIdx = remainingItems.length;
                        if (targetSiblingId !== null) {
                            const idx = remainingItems.findIndex(c => c.id === targetSiblingId);
                            if (idx !== -1) insertIdx = idx;
                        }
                        
                        remainingItems.splice(insertIdx, 0, ...draggedItems);
                        
                        const order = remainingItems.map((item, index) => {
                            const newSort = (this.liveFilter.page - 1) * this.liveFilter.limit + index;
                            return { id: item.id, sort_index: newSort };
                        });
                        
                        this.liveChannels = remainingItems;
                        this.tbodyKey++;
                        
                        this.$nextTick(async () => {
                            this.initSortable();
                            try {
                                const r = await fetch('/api/live/channels/reorder', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ order })
                                });
                                if (r.ok) {
                                    this.showToast(isBatch ? `批量排序成功 (${dragIds.length} 个频道)` : '排序更新成功');
                                    this.fetchLiveStats();
                                } else {
                                    this.showToast('保存排序失败', 'error');
                                }
                            } catch(e) {
                                this.showToast('排序请求失败', 'error');
                            }
                        });
                    }
                });
            }
        }
    }
});

app.mount('#app');
