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
            activeTab: localStorage.getItem('active_tab') || 'stb',
            theme: 'dark',

            // Toast
            toast: { show: false, message: '', type: 'success', timeoutId: null },

            // Tab info
            tabTitles: {
                stb: '系统凭证配置',
                sync: '数据同步管理',
                live: '直播频道管理',
                log: '系统日志'
            },
            tabSubtitles: {
                stb: '配置电信机顶盒仿真认证参数，保障安全接入 EPG 网关',
                sync: '从 VIS API 同步点播数据到本地 SQLite 数据库',
                live: '管理直播频道、分类、外部导入，生成 M3U 订阅',
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

            // Plate 4: Live Channel Management
            liveFilter: {
                category_id: null, enabled: null, source: null, keyword: '', page: 1, limit: 10000
            },
            liveChannels: [],
            liveTotal: 0,
            liveCategories: [],
            liveConfig: {
                udpxy_address: '', epg_url: '', logo_base_url: '',
                fcc_global_enabled_bool: false, timeshift_enabled_bool: false, m3u_dual_line_bool: false
            },
            newCategory: { name: '', sort_index: 0, color: '#6366f1', is_visible: 1 },
            editingCh: null,
            showLiveConfigModal: false,
            showCategoryModal: false,
            showEditChannelModal: false,
            syncingLive: false,
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
            return `${origin}/api/live/tv.m3u`;
        }
    },

    watch: {
        activeTab(newTab) {
            localStorage.setItem('active_tab', newTab);
            this.stopAllPolling();
            if (newTab === 'stb') {
                this.startSimStatusPolling();
            } else if (newTab === 'log') {
                this.startLogPolling();
            } else if (newTab === 'sync') {
                this.startSyncStatusPolling();
            } else if (newTab === 'live') {
                this.initLiveTab();
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
                    m3u_dual_line_bool: config.m3u_dual_line === '1'
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
                m3u_dual_line: this.liveConfig.m3u_dual_line_bool ? '1' : '0'
            };
            delete payload.udpxy_enabled_bool;
            delete payload.fcc_global_enabled_bool;
            delete payload.timeshift_enabled_bool;
            delete payload.m3u_dual_line_bool;

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

        openCategoryModal() {
            this.showCategoryModal = true;
            this.newCategory = { name: '', sort_index: 0, color: '#6366f1', is_visible: 1 };
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
                    this.fetchLiveCategories();
                    this.newCategory = { name: '', sort_index: 0, color: '#6366f1', is_visible: 1 };
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
                    this.fetchLiveCategories();
                    this.fetchLiveChannels(this.liveFilter.page);
                } else {
                    const data = await r.json();
                    this.showToast(data.detail || '修改失败', 'error');
                }
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        async deleteLiveCategory(id) {
            if (!confirm('确定要删除此分类吗？关联的频道将自动归入“未分类”。')) return;
            try {
                const r = await fetch(`/api/live/categories/${id}`, { method: 'DELETE' });
                if (r.ok) {
                    this.showToast('分类删除成功');
                    this.fetchLiveCategories();
                    this.fetchLiveChannels(this.liveFilter.page);
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
