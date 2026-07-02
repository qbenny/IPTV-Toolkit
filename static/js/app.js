/**
 * IPTV-Toolkit v2.0 前端逻辑
 * - 系统凭证配置
 * - 数据同步管理
 * - 直播频道管理
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
                live: '直播频道管理',
                log: '系统日志'
            },
            tabSubtitles: {
                stb: '配置电信机顶盒仿真认证参数，保障安全接入 EPG 网关',
                sync: '从 VIS API 同步点播数据到本地 SQLite 数据库',
                live: '管理直播频道列表、同步服务器频道、导入外部频道、生成 M3U 播放列表',
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

            // Plate 4: Live Channels
            liveConfig: {},
            liveCategories: [],
            liveChannels: [],
            liveStats: {},
            liveCategoryFilter: '',
            liveSourceFilter: '',
            liveEnabledFilter: '',
            liveSearchText: '',
            liveSyncRunning: false,
            livePollTimer: null,

            // Multi-select
            selectedIds: new Set(),
            showBatchCategoryModal: false,
            batchCategoryId: 0,

            // Category management
            showCategoryAddModal: false,
            showCategoryEditModal: false,
            showCategoryDeleteModal: false,
            categoryFormName: '',

            // Edit modal
            showEditModal: false,
            editChannel: {},

            // Settings modal
            showSettingsModal: false,
            settingsForm: {},

            // Delete confirm
            showDeleteConfirm: false,
            deleteTarget: null,
            deleteMode: 'single',  // 'single' | 'batch' | 'all'

            // Import
            importMode: 'paste',
            importContent: '',
            importUrl: '',
            importFileName: '',
            importFileContent: '',
            importPreview: [],
            importing: false,

            // Sortable
            sortable: null,

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
        m3uUrl() {
            return window.location.origin + '/api/live/tv.m3u';
        },
        logoBaseUrl() {
            const config = this.liveConfig || {};
            return config.logo_base_url || '/static/logo/';
        },
        categoryMap() {
            const map = {};
            (this.liveCategories || []).forEach(c => { map[c.id] = c.name; });
            return map;
        },
        filteredChannels() {
            let list = this.liveChannels || [];
            const catId = this.liveCategoryFilter ? parseInt(this.liveCategoryFilter) : null;
            if (catId) list = list.filter(ch => ch.category_id === catId);
            if (this.liveSourceFilter) list = list.filter(ch => ch.source === this.liveSourceFilter);
            if (this.liveEnabledFilter !== '') list = list.filter(ch => ch.is_enabled === parseInt(this.liveEnabledFilter));
            if (this.liveSearchText) {
                const kw = this.liveSearchText.toLowerCase();
                list = list.filter(ch => ch.name && ch.name.toLowerCase().includes(kw));
            }
            return list;
        },
        isAllSelected() {
            const selectable = this.filteredChannels.filter(ch => ch.source === 'external');
            if (!selectable.length) return false;
            return selectable.every(ch => this.selectedIds.has(ch.id));
        },
        selectedCount() {
            return this.selectedIds.size;
        },
        udpxyAddress() {
            return (this.liveConfig && this.liveConfig.udpxy_address) || '';
        },
        udpxyEnabled() {
            return (this.liveConfig && this.liveConfig.udpxy_enabled === '1');
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
            } else if (newTab === 'live') {
                this.fetchLiveConfig();
                this.fetchLiveCategories();
                this.fetchLiveChannels();
                this.fetchLiveStats();
                this.startLivePolling();
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
        if (this.sortable) this.sortable.destroy();
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
            [this.simStatusTimer, this.syncStatusTimer, this.logPollTimer, this.livePollTimer].forEach(t => {
                if (t) { clearInterval(t); }
            });
            this.simStatusTimer = this.syncStatusTimer = this.logPollTimer = this.livePollTimer = null;
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
            return token.substring(0, 6) + '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' + token.substring(token.length - 6);
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
            if (!ts) return '\u2014';
            return new Date(ts * 1000).toLocaleString('zh-CN');
        },

        // ============================================================
        // Live Channels
        // ============================================================

        startLivePolling() {
            if (!this.livePollTimer) this.livePollTimer = setInterval(() => {
                this.fetchLiveChannels();
                this.fetchLiveStats();
            }, 8000);
        },

        async fetchLiveConfig() {
            try {
                const r = await fetch('/api/live/config');
                this.liveConfig = await r.json();
            } catch (e) { /* silent */ }
        },

        async fetchLiveCategories() {
            try {
                const r = await fetch('/api/live/categories');
                this.liveCategories = await r.json();
            } catch (e) { /* silent */ }
        },

        async fetchLiveChannels() {
            try {
                const params = new URLSearchParams();
                if (this.liveCategoryFilter) params.set('category_id', this.liveCategoryFilter);
                if (this.liveSourceFilter) params.set('source', this.liveSourceFilter);
                if (this.liveEnabledFilter !== '') params.set('enabled', this.liveEnabledFilter);
                params.set('limit', '9999');
                const r = await fetch('/api/live/channels?' + params.toString());
                const data = await r.json();
                this.liveChannels = data.list || [];
                // 清理已不存在的选中项
                const currentIds = new Set((data.list || []).map(ch => ch.id));
                for (const id of this.selectedIds) {
                    if (!currentIds.has(id)) this.selectedIds.delete(id);
                }
                this.$nextTick(() => this.initSortable());
            } catch (e) { /* silent */ }
        },

        async fetchLiveStats() {
            try {
                const r = await fetch('/api/live/stats');
                this.liveStats = await r.json();
            } catch (e) { /* silent */ }
        },

        // ---- Multi-select ----
        toggleSelect(id) {
            if (this.selectedIds.has(id)) {
                this.selectedIds.delete(id);
            } else {
                this.selectedIds.add(id);
            }
        },
        toggleSelectAll() {
            if (this.isAllSelected) {
                // 取消全选
                this.filteredChannels.forEach(ch => {
                    if (ch.source === 'external') this.selectedIds.delete(ch.id);
                });
            } else {
                // 全选
                this.filteredChannels.forEach(ch => {
                    if (ch.source === 'external') this.selectedIds.add(ch.id);
                });
            }
        },

        // ---- Computed unicast URL (udpxy proxy) ----
        computedUnicastUrl(ch) {
            if (!ch.multicast_url) return '';
            const url = ch.multicast_url;
            if (url.startsWith('igmp://') && this.udpxyAddress && this.udpxyEnabled) {
                let addr = this.udpxyAddress.trim();
                if (addr.startsWith('http://')) addr = addr.substring(7);
                if (addr.startsWith('https://')) addr = addr.substring(8);
                return 'http://' + addr + '/udp/' + url.substring(7);
            }
            return '';
        },

        // ---- Category management ----
        openCategoryAdd() {
            this.categoryFormName = '';
            this.showCategoryAddModal = true;
        },
        async doCategoryAdd() {
            const name = this.categoryFormName.trim();
            if (!name) { this.showToast('请输入分类名称', 'error'); return; }
            try {
                const r = await fetch('/api/live/categories', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, sort_index: 99 })
                });
                if (r.ok) {
                    this.showToast('分类已新增');
                    this.showCategoryAddModal = false;
                    await this.fetchLiveCategories();
                } else {
                    const res = await r.json();
                    this.showToast(res.error || '新增失败', 'error');
                }
            } catch (e) { this.showToast('通信异常', 'error'); }
        },
        openCategoryEdit() {
            if (!this.liveCategoryFilter) return;
            this.categoryFormName = this.categoryMap[this.liveCategoryFilter] || '';
            this.showCategoryEditModal = true;
        },
        async doCategoryEdit() {
            const name = this.categoryFormName.trim();
            if (!name || !this.liveCategoryFilter) { this.showToast('请输入分类名称', 'error'); return; }
            try {
                const r = await fetch('/api/live/categories/' + this.liveCategoryFilter, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                if (r.ok) {
                    this.showToast('分类已更新');
                    this.showCategoryEditModal = false;
                    await this.fetchLiveCategories();
                    await this.fetchLiveChannels();
                } else { this.showToast('修改失败', 'error'); }
            } catch (e) { this.showToast('通信异常', 'error'); }
        },
        openCategoryDelete() {
            if (!this.liveCategoryFilter) return;
            this.showCategoryDeleteModal = true;
        },
        async doCategoryDelete() {
            if (!this.liveCategoryFilter) return;
            try {
                const r = await fetch('/api/live/categories/' + this.liveCategoryFilter, { method: 'DELETE' });
                if (r.ok) {
                    this.showToast('分类已删除');
                    this.showCategoryDeleteModal = false;
                    this.liveCategoryFilter = '';
                    await this.fetchLiveCategories();
                    await this.fetchLiveChannels();
                } else {
                    const res = await r.json();
                    this.showToast(res.error || '删除失败', 'error');
                }
            } catch (e) { this.showToast('通信异常', 'error'); }
        },

        // ---- Sync ----
        async triggerLiveSync() {
            if (!this.simStatus.is_authenticated) {
                this.showToast('请先在「系统凭证配置」页面完成登录认证', 'error');
                return;
            }
            this.liveSyncRunning = true;
            try {
                const r = await fetch('/api/live/sync', { method: 'POST' });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message || '同步完成');
                } else {
                    this.showToast(res.error || '同步失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            } finally {
                this.liveSyncRunning = false;
                await this.fetchLiveChannels();
                await this.fetchLiveStats();
            }
        },

        // ---- Toggle enable ----
        async toggleChannel(ch) {
            const newVal = ch.is_enabled ? 0 : 1;
            try {
                const r = await fetch('/api/live/channels/' + ch.id, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ is_enabled: newVal })
                });
                if (r.ok) {
                    ch.is_enabled = newVal;
                    this.showToast(newVal ? ch.name + ' 已启用' : ch.name + ' 已禁用');
                    this.fetchLiveStats();
                }
            } catch (e) {
                this.showToast('操作失败', 'error');
            }
        },

        // ---- Edit Modal ----
        openEditModal(ch) {
            this.editChannel = { ...ch };
            this.showEditModal = true;
        },

        async saveChannelEdit() {
            try {
                const r = await fetch('/api/live/channels/' + this.editChannel.id, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.editChannel.name,
                        tvg_id: this.editChannel.tvg_id,
                        tvg_name: this.editChannel.tvg_name,
                        logo_url: this.editChannel.logo_url,
                        category_id: this.editChannel.category_id,
                        sort_index: this.editChannel.sort_index
                    })
                });
                if (r.ok) {
                    this.showToast('频道已更新');
                    this.showEditModal = false;
                    await this.fetchLiveChannels();
                } else {
                    this.showToast('更新失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            }
        },

        // ---- Delete ----
        confirmDeleteChannel(ch) {
            this.deleteTarget = ch;
            this.deleteMode = 'single';
            this.showDeleteConfirm = true;
        },

        confirmBatchDelete() {
            if (this.selectedIds.size === 0) {
                this.showToast('未选中任何频道', 'error');
                return;
            }
            this.deleteMode = 'batch';
            this.showDeleteConfirm = true;
        },

        confirmDeleteAllExternal() {
            if (!this.liveStats.external_total) {
                this.showToast('没有可删除的外部频道', 'error');
                return;
            }
            this.deleteMode = 'all';
            this.showDeleteConfirm = true;
        },

        async doDeleteChannel() {
            try {
                let result;
                if (this.deleteMode === 'single') {
                    result = await fetch('/api/live/channels/' + this.deleteTarget.id, { method: 'DELETE' });
                } else if (this.deleteMode === 'batch') {
                    const ids = [...this.selectedIds];
                    result = await fetch('/api/live/channels/batch-delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ids })
                    });
                } else if (this.deleteMode === 'all') {
                    result = await fetch('/api/live/channels/external/all', { method: 'DELETE' });
                }

                if (result && result.ok) {
                    const data = await result.json();
                    this.showToast(`已删除 ${data.count || 0} 个频道`);
                    this.showDeleteConfirm = false;
                    this.deleteTarget = null;
                    this.selectedIds.clear();
                    await this.fetchLiveChannels();
                    await this.fetchLiveStats();
                } else {
                    const err = result ? await result.json() : { error: '操作失败' };
                    this.showToast(err.error || '删除失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            }
        },

        // ---- Batch Category ----
        openBatchCategoryModal() {
            if (this.selectedIds.size === 0) {
                this.showToast('未选中任何频道', 'error');
                return;
            }
            this.batchCategoryId = 0;
            this.showBatchCategoryModal = true;
        },

        async doBatchCategory() {
            if (this.selectedIds.size === 0) return;
            try {
                const r = await fetch('/api/live/channels/batch-category', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ids: [...this.selectedIds],
                        category_id: this.batchCategoryId
                    })
                });
                if (r.ok) {
                    const data = await r.json();
                    const catName = this.categoryMap[this.batchCategoryId] || '未分类';
                    this.showToast(`${data.count} 个频道已移到「${catName}」`);
                    this.showBatchCategoryModal = false;
                    this.selectedIds.clear();
                    await this.fetchLiveChannels();
                } else {
                    this.showToast('操作失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            }
        },

        // ---- Settings Modal ----
        async openSettingsModal() {
            await this.fetchLiveConfig();
            this.settingsForm = { ...this.liveConfig };
            this.showSettingsModal = true;
        },

        async saveSettings() {
            try {
                const r = await fetch('/api/live/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.settingsForm)
                });
                if (r.ok) {
                    this.liveConfig = { ...this.settingsForm };
                    this.showToast('设置已保存');
                    this.showSettingsModal = false;
                } else {
                    this.showToast('保存失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            }
        },

        // ---- Import ----
        onImportFileSelected(e) {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            this.importFileName = file.name;
            const reader = new FileReader();
            reader.onload = (ev) => {
                this.importFileContent = ev.target.result;
            };
            reader.readAsText(file);
        },

        async previewImport() {
            try {
                let body;
                if (this.importMode === 'url') {
                    if (!this.importUrl || !this.importUrl.trim()) {
                        this.showToast('请先输入链接', 'error');
                        return;
                    }
                    body = JSON.stringify({ url: this.importUrl });
                } else if (this.importMode === 'file') {
                    if (!this.importFileContent || !this.importFileContent.trim()) {
                        this.showToast('请先选择文件', 'error');
                        return;
                    }
                    body = JSON.stringify({ content: this.importFileContent });
                } else {
                    if (!this.importContent || !this.importContent.trim()) {
                        this.showToast('内容为空', 'error');
                        return;
                    }
                    body = JSON.stringify({ content: this.importContent });
                }
                const r = await fetch('/api/live/import/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: body
                });
                const res = await r.json();
                if (r.ok) {
                    this.importPreview = res.channels || [];
                    if (this.importPreview.length === 0) {
                        this.showToast('未识别到任何频道', 'error');
                    }
                } else {
                    this.showToast(res.error || '预览失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            }
        },

        async doImport() {
            try {
                let body;
                if (this.importMode === 'url') {
                    if (!this.importUrl || !this.importUrl.trim()) {
                        this.showToast('请先输入链接', 'error');
                        return;
                    }
                    body = JSON.stringify({ url: this.importUrl });
                } else if (this.importMode === 'file') {
                    if (!this.importFileContent || !this.importFileContent.trim()) {
                        this.showToast('请先选择文件', 'error');
                        return;
                    }
                    body = JSON.stringify({ content: this.importFileContent });
                } else {
                    if (!this.importContent || !this.importContent.trim()) {
                        this.showToast('内容为空', 'error');
                        return;
                    }
                    body = JSON.stringify({ content: this.importContent });
                }

                this.importing = true;
                const r = await fetch('/api/live/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: body
                });
                const res = await r.json();
                if (r.ok) {
                    this.showToast(res.message || '导入完成');
                    this.importContent = '';
                    this.importUrl = '';
                    this.importFileContent = '';
                    this.importFileName = '';
                    this.importPreview = [];
                    if (this.$refs.importFileInput) this.$refs.importFileInput.value = '';
                    await this.fetchLiveChannels();
                    await this.fetchLiveStats();
                } else {
                    this.showToast(res.error || '导入失败', 'error');
                }
            } catch (e) {
                this.showToast('通信异常', 'error');
            } finally { this.importing = false; }
        },

        // ---- SortableJS ----
        initSortable() {
            if (this.sortable) this.sortable.destroy();
            const el = document.getElementById('channelSortable');
            if (!el || !window.Sortable) return;

            this.sortable = new Sortable(el, {
                handle: '.drag-handle',
                animation: 200,
                ghostClass: 'sortable-ghost',
                chosenClass: 'sortable-chosen',
                filter: 'input[type="checkbox"]',  // 避免拖拽触发 checkbox
                preventOnFilter: false,
                onEnd: (evt) => {
                    const items = [...el.querySelectorAll('tr[data-id]')];
                    const order = items.map((tr, idx) => ({
                        id: parseInt(tr.getAttribute('data-id')),
                        sort_index: idx
                    }));
                    fetch('/api/live/channels/reorder', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ order: order })
                    }).catch(() => {});
                }
            });
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
