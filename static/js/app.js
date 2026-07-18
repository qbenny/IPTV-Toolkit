/**
 * IPTV-Toolkit v2.0 前端逻辑
 * - 系统配置
 * - 数据同步管理
 * - 系统日志查看
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
            formatTime: this.formatTime
        };
    },
    data() {
        return {
            activeTab: (() => { const t = localStorage.getItem('active_tab'); return t === 'sync' ? 'vod' : t || 'stb'; })(),
            theme: 'dark',



            // Tab info
            tabTitles: {
                stb: '系统配置',
                vod: 'VOD 点播管理',
                live: '直播频道管理',
                epg: 'EPG 节目管理'
            },
            tabSubtitles: {
                stb: '管理机顶盒仿真认证凭证、定时同步任务与系统日志',
                vod: '从 VIS API 同步点播数据到本地 SQLite 数据库',
                live: '管理直播频道、分类、外部导入，生成 M3U 订阅',
                epg: '从 VIS 节目单 API 同步 EPG 数据，生成 XMLTV'
            },

            // Plate 1: Core
            simStatus: { is_authenticated: false, epg_base_url: null, user_token: null, jsessionid: null },
            simStatusTimer: null,

            // Plate 2: Live Channel Management
            liveFilter: {
                category_id: null, enabled: null, source: null, keyword: '', page: 1, limit: 10000
            },
            liveChannels: [],
            liveTotal: 0,
            liveCategories: [],
            liveConfig: {
                udpxy_address: '', logo_base_url: '',
                fcc_global_enabled_bool: false, timeshift_enabled_bool: false,
                m3u_dual_line_bool: false, udpxy_enabled_bool: false
            },
            loadingConfig: false,
            newCategory: { name: '', sort_index: 0, color: '#6366f1', is_visible: 1 },
            editingCh: null,
            showAliasModal: false,
            aliases: [],
            newAlias: { source_name: '', target_name: '' },
            showLiveConfigModal: false,
            showCategoryModal: false,
            categorySortableInstance: null,
            categoryTbodyKey: 0,
            showEditChannelModal: false,
            syncingLive: false,
            importMethod: 'text',
            importText: '',
            importFile: null,
            importingChannels: false,
            liveStats: { server: 0, external: 0, enabled: 0, disabled: 0 },
            sortableInstance: null,
            selectedChannelIds: [],
            selectAllChannels: false,
            tbodyKey: 0,

            // EPG Preview Modal
            showEpgPreviewModal: false,
            epgPreviewChannel: null,
            epgPreviewDates: [],
            epgPreviewDateIndex: 0,
            epgLoading: false,
            epgPrograms: [],

        };
    },

    computed: {
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
        this.fetchSimStatus();
        if (this.activeTab === 'stb') {
            this.startSimStatusPolling();
        } else if (this.activeTab === 'live') {
            this.initLiveTab();
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
                const data = await epgService.getPrograms(channelId, dateStr);
                this.epgPrograms = data.items || [];
                this.$nextTick(() => { this.scrollToPlayingProgram(); });
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
            const toast = this.$refs.toastRef;
            if (toast) toast.show(msg, type);
        },

        // ---- Polling ----
        stopAllPolling() {
            if (this.simStatusTimer) { clearInterval(this.simStatusTimer); this.simStatusTimer = null; }
        },



        // ---- Sim Status (global poll) ----
        async fetchSimStatus() {
            try {
                const d = await stbService.getSimStatus();
                this.simStatus = d;
            } catch (e) { /* silent */ }
        },

        startSimStatusPolling() {
            this.fetchSimStatus();
            if (!this.simStatusTimer) this.simStatusTimer = setInterval(() => this.fetchSimStatus(), 5000);
        },

        // ---- Sync (VOD/EPG) light wrappers (StbTab manual sync uses these) ----
        async triggerSync() {
            try { const res = await syncService.triggerSync(); this.showToast(res.message || '同步已启动'); }
            catch (e) { this.showToast(e.message || '通信异常', 'error'); }
        },
        async triggerEpgSync() {
            try { const res = await epgService.triggerSync(); this.showToast(res.message || 'EPG 同步已启动'); }
            catch (e) { this.showToast(e.message || '通信异常', 'error'); }
        },

        formatTime(ts) {
            if (!ts) return '—';
            return new Date(ts * 1000).toLocaleString('zh-CN');
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
                const data = await liveService.getChannels({
                    category_id: this.liveFilter.category_id,
                    enabled: this.liveFilter.enabled,
                    source: this.liveFilter.source,
                    keyword: this.liveFilter.keyword || undefined,
                    page: this.liveFilter.page,
                    limit: this.liveFilter.limit
                });
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
                this.liveCategories = await liveService.getCategories();
            } catch (e) { /* silent */ }
        },

        async fetchLiveConfig() {
            this.loadingConfig = true;
            try {
                const config = await liveService.getConfig();
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
                this.liveStats = await liveService.getStats();
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
                const res = await liveService.triggerSync();
                this.showToast(res.message);
                this.initLiveTab();
            } catch (e) {
                this.showToast(e.message || '网络请求异常', 'error');
            } finally {
                this.syncingLive = false;
            }
        },

        async toggleChannelEnabled(ch) {
            const next_enabled = ch.is_enabled === 1 ? 0 : 1;
            try {
                await liveService.updateChannel(ch.id, { is_enabled: next_enabled });
                ch.is_enabled = next_enabled;
                this.showToast(ch.is_enabled ? '频道已启用' : '频道已禁用');
                this.fetchLiveStats();
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
            }
        },

        async saveChannelEdit() {
            if (!this.editingCh) return;
            try {
                await liveService.updateChannel(this.editingCh.id, this.editingCh);
                this.showToast('频道修改成功');
                this.showEditChannelModal = false;
                this.fetchLiveChannels(this.liveFilter.page);
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
            }
        },

        async changeChannelCategory(ch) {
            try {
                await liveService.updateChannel(ch.id, { category_id: ch.category_id });
                this.showToast('分类已更新');
                this.fetchLiveChannels(this.liveFilter.page);
            } catch (e) {
                this.showToast('网络错误', 'error');
            }
        },

        async deleteChannel(id) {
            if (!confirm('确定要删除这个外部频道吗？')) return;
            try {
                await liveService.deleteChannel(id);
                this.showToast('频道删除成功');
                this.fetchLiveChannels(this.liveFilter.page);
                this.fetchLiveStats();
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
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
                await liveService.saveConfig(payload);
                this.showToast('直播配置保存成功');
                this.showLiveConfigModal = false;
                this.fetchLiveChannels(this.liveFilter.page);
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
                color: '#6366f1',
                is_visible: 1
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
                            await liveService.reorderCategories({ order });
                            this.showToast('分类排序已更新');
                                // 从服务器重新加载以保持数据一致
                                await this.fetchLiveCategories();
                                this.categoryTbodyKey++;
                                this.$nextTick(() => { this.initCategorySortable(); });
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
                await liveService.addCategory(this.newCategory);
                this.showToast('分类添加成功');
                await this.fetchLiveCategories();
                const nextIdx = this.liveCategories.length > 0
                    ? Math.max(...this.liveCategories.map(c => c.sort_index)) + 1
                    : 0;
                this.newCategory = { name: '', sort_index: nextIdx, color: '#6366f1', is_visible: 1 };
                this.categoryTbodyKey++;
                this.$nextTick(() => { this.initCategorySortable(); });
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
            }
        },

        async updateLiveCategory(cat) {
            try {
                await liveService.updateCategory(cat.id, cat);
                this.showToast('分类修改成功');
                await this.fetchLiveCategories();
                this.fetchLiveChannels(this.liveFilter.page);
                this.categoryTbodyKey++;
                this.$nextTick(() => { this.initCategorySortable(); });
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
            }
        },

        async deleteLiveCategory(id) {
            if (!confirm('确定要删除此分类吗？关联的频道将自动归入"未分类"。')) return;
            try {
                await liveService.deleteCategory(id);
                this.showToast('分类删除成功');
                await this.fetchLiveCategories();
                this.fetchLiveChannels(this.liveFilter.page);
                this.categoryTbodyKey++;
                this.$nextTick(() => { this.initCategorySortable(); });
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
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
                let res;
                if (this.importMethod === 'file') {
                    if (!this.importFile) {
                        this.showToast('请先选择要上传的文件', 'error');
                        return;
                    }
                    const formData = new FormData();
                    formData.append('file', this.importFile);
                    res = await liveService.importChannels(formData);
                    this.showToast(`导入成功！新增 ${res.new} 个，跳过 ${res.skipped} 个，总共 ${res.total} 个`);
                    this.importFile = null;
                } else {
                    if (!this.importText.trim()) {
                        this.showToast('请粘贴文本内容再进行导入', 'error');
                        return;
                    }
                    res = await liveService.importChannels({ content: this.importText });
                    this.showToast(`导入成功！新增 ${res.new} 个，跳过 ${res.skipped} 个，总共 ${res.total} 个`);
                    this.importText = '';
                }
                this.initLiveTab();
            } catch (e) {
                this.showToast(e.message || '导入过程中遭遇异常', 'error');
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
                this.aliases = await liveService.getAliases();
            } catch(e) {}
        },
        async addAlias() {
            if (!this.newAlias.source_name.trim() || !this.newAlias.target_name.trim()) {
                this.showToast('原始名称和规范名称均不能为空', 'error'); return;
            }
            try {
                const res = await liveService.addAlias(this.newAlias);
                this.showToast('别名添加成功' + (res.affected_channels ? '，已更新 ' + res.affected_channels + ' 个频道' : ''));
                this.newAlias = { source_name: '', target_name: '' };
                this.fetchAliases();
                this.fetchLiveChannels(this.liveFilter.page);
            } catch(e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async saveAlias(a) {
            try {
                const res = await liveService.updateAlias(a.id, { source_name: a.source_name, target_name: a.target_name });
                this.showToast('别名保存成功' + (res.affected_channels ? '，已更新 ' + res.affected_channels + ' 个频道' : ''));
                this.fetchLiveChannels(this.liveFilter.page);
            } catch(e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async deleteAlias(id) {
            if (!confirm('确定要删除这个别名映射吗？相关频道将恢复原始名称。')) return;
            try {
                await liveService.deleteAlias(id);
                this.showToast('别名删除成功，频道已恢复原始名称');
                this.fetchAliases();
                this.fetchLiveChannels(this.liveFilter.page);
            } catch(e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async reapplyAliases() {
            try {
                const res = await liveService.reapplyAliases();
                this.showToast(`已重新应用：${res.applied} 条映射，${res.affected} 个频道`);
                this.fetchLiveChannels(this.liveFilter.page);
            } catch(e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async exportAliases() {
            try {
                const data = await liveService.exportAliases();
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
                const res = await liveService.importAliases(data);
                this.showToast(`导入成功，共 ${res.imported} 条映射`);
                this.fetchAliases();
            } catch(e) { this.showToast('文件解析失败，请检查 JSON 格式', 'error'); }
            e.target.value = '';
        },

        async exportCategoryMappings() {
            try {
                const data = await liveService.exportCategoryMappings();
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
                const res = await liveService.importCategoryMappings(data);
                this.showToast(`导入成功：关联了 ${res.imported_channels} 个频道的分类` + (res.created_categories ? `，自动创建了 ${res.created_categories} 个新分类` : ''));
                this.fetchLiveCategories();
                this.fetchLiveChannels(this.liveFilter.page);
            } catch(e) { this.showToast(e.message || '文件解析失败，请检查 JSON 格式', 'error'); }
            e.target.value = '';
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
                const res = await liveService.batchEnabled(this.selectedChannelIds, enabled);
                this.showToast(res.message);
                this.selectedChannelIds = [];
                this.fetchLiveChannels(this.liveFilter.page);
                this.fetchLiveStats();
            } catch (e) {
                this.showToast(e.message || '网络请求异常', 'error');
            }
        },

        async batchChangeCategory(event) {
            const catId = parseInt(event.target.value);
            if (isNaN(catId) || this.selectedChannelIds.length === 0) return;
            try {
                const res = await liveService.batchCategory(this.selectedChannelIds, catId);
                this.showToast(res.message);
                this.selectedChannelIds = [];
                event.target.value = '';
                this.fetchLiveChannels(this.liveFilter.page);
            } catch (e) {
                this.showToast(e.message || '网络请求异常', 'error');
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
                const res = await liveService.batchDelete(this.selectedChannelIds);
                this.showToast(res.message);
                this.selectedChannelIds = [];
                this.fetchLiveChannels(this.liveFilter.page);
                this.fetchLiveStats();
            } catch (e) {
                this.showToast(e.message || '网络请求异常', 'error');
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
                await liveService.reorderChannels({ order });
                this.showToast('已按类别排序');
                this.fetchLiveChannels(1);
            } catch (e) {
                this.showToast(e.message || '网络异常', 'error');
            }
        },

        async resetLiveChannelsOrder() {
            if (!confirm('确定要恢复默认排序吗？这会清除拖拽自定义顺序，并按系统序号/ID恢复初始顺序。')) return;
            try {
                const res = await liveService.resetOrder();
                this.showToast(res.message);
                this.fetchLiveChannels(1);
            } catch (e) {
                this.showToast(e.message || '网络连接异常', 'error');
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
                                await liveService.reorderChannels({ order });
                                this.showToast(isBatch ? `批量排序成功 (${dragIds.length} 个频道)` : '排序更新成功');
                                this.fetchLiveStats();
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

// 注册共享组件
app.component('toast-notification', ToastNotification);
app.component('color-picker', ColorPicker);
app.component('sidebar-nav', SidebarNav);
app.component('stb-tab', StbTab);
app.component('vod-tab', VodTab);
app.component('epg-tab', EpgTab);

app.mount('#app');
