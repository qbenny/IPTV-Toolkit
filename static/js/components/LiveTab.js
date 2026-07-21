/**
 * LiveTab — 直播频道管理 Tab
 * Inject: showToast, simStatus (跨 Tab 共享)
 * 内置 EPG 节目单预览弹窗
 */
const LiveTab = {
    name: 'LiveTab',
    inject: ['showToast', 'simStatus'],

    data() {
        return {
            /* 频道管理 */
            liveFilter: { category_id: null, enabled: null, source: null, keyword: '', page: 1, limit: 10000 },
            liveChannels: [], liveTotal: 0,
            liveCategories: [],
            liveConfig: { udpxy_address: '', logo_base_url: '', fcc_global_enabled_bool: false, timeshift_enabled_bool: false, m3u_dual_line_bool: false, udpxy_enabled_bool: false },
            loadingConfig: false,
            liveStats: { server: 0, external: 0, enabled: 0, disabled: 0 },
            selectedChannelIds: [], selectAllChannels: false,
            tbodyKey: 0, sortableInstance: null,
            syncingLive: false,
            editingCh: null, showEditChannelModal: false,

            /* 分类 */
            showCategoryModal: false,
            newCategory: { name: '', sort_index: 0, color: '#6366f1', is_visible: 1 },
            categorySortableInstance: null, categoryTbodyKey: 0,

            /* 导入 */
            importMethod: 'text', importText: '', importFile: null, importingChannels: false,

            /* 别名 */
            showAliasModal: false, aliases: [], newAlias: { source_name: '', target_name: '' },

            /* 设置 */
            showLiveConfigModal: false,

            /* EPG 预览 */
            showEpgPreviewModal: false,
            epgPreviewChannel: null,
            epgPreviewDates: [],
            epgPreviewDateIndex: 0,
            epgLoading: false,
            epgPrograms: [],
        };
    },

    computed: {
        canBatchDelete() {
            if (this.selectedChannelIds.length === 0) return false;
            return this.selectedChannelIds.every(id => {
                const ch = this.liveChannels.find(c => c.id === id);
                return ch && ch.source === 'external';
            });
        },
        epgFormattedDate() {
            if (this.epgPreviewDates.length === 0) return '';
            return this.formatEpgDateLabel(this.epgPreviewDates[this.epgPreviewDateIndex]);
        },
    },

    watch: {
        selectedChannelIds(newVal) {
            if (newVal.length === 0) { this.selectAllChannels = false; }
            else if (this.liveChannels.length > 0 && newVal.length === this.liveChannels.length) { this.selectAllChannels = true; }
            else { this.selectAllChannels = false; }
        },
        'liveConfig.udpxy_enabled_bool'(newVal) {
            if (this.loadingConfig) return;
            this.liveConfig.fcc_global_enabled_bool = newVal ? true : false;
        }
    },

    created() {
        this.initLiveTab();
    },

    beforeUnmount() {
        if (this.sortableInstance) { this.sortableInstance.destroy(); this.sortableInstance = null; }
        if (this.categorySortableInstance) { this.categorySortableInstance.destroy(); this.categorySortableInstance = null; }
    },

    methods: {
        /* ========== 初始化 ========== */
        initLiveTab() {
            this.fetchLiveCategories();
            this.fetchLiveConfig();
            this.fetchLiveChannels(1);
            this.fetchLiveStats();
        },

        /* ========== 频道 CRUD ========== */
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
                this.tbodyKey++; this.selectedChannelIds = []; this.selectAllChannels = false;
                this.$nextTick(() => { this.initSortable(); });
            } catch (e) { this.showToast('加载直播频道失败', 'error'); }
        },
        async fetchLiveCategories() { try { this.liveCategories = await liveService.getCategories(); } catch (e) {} },
        async fetchLiveConfig() {
            this.loadingConfig = true;
            try {
                const config = await liveService.getConfig();
                this.liveConfig = { ...config, udpxy_enabled_bool: config.udpxy_enabled === '1', fcc_global_enabled_bool: config.fcc_global_enabled === '1', timeshift_enabled_bool: config.timeshift_enabled === '1', m3u_dual_line_bool: config.m3u_dual_line === '1' };
            } catch (e) {} finally { this.loadingConfig = false; this.$nextTick(() => { this.loadingConfig = false; }); }
        },
        async fetchLiveStats() { try { this.liveStats = await liveService.getStats(); } catch (e) {} },

        async triggerLiveSync() {
            this.syncingLive = true;
            try { const res = await liveService.triggerSync(); this.showToast(res.message); this.initLiveTab(); }
            catch (e) { this.showToast(e.message || '网络请求异常', 'error'); }
            finally { this.syncingLive = false; }
        },

        async toggleChannelEnabled(ch) {
            const next = ch.is_enabled === 1 ? 0 : 1;
            try { await liveService.updateChannel(ch.id, { is_enabled: next }); ch.is_enabled = next; this.showToast(next ? '频道已启用' : '频道已禁用'); this.fetchLiveStats(); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async saveChannelEdit() {
            if (!this.editingCh) return;
            try { await liveService.updateChannel(this.editingCh.id, this.editingCh); this.showToast('频道修改成功'); this.showEditChannelModal = false; this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async changeChannelCategory(ch) {
            try { await liveService.updateChannel(ch.id, { category_id: ch.category_id }); this.showToast('分类已更新'); this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast('网络错误', 'error'); }
        },
        async deleteChannel(id) {
            if (!confirm('确定要删除这个外部频道吗？')) return;
            try { await liveService.deleteChannel(id); this.showToast('频道删除成功'); this.fetchLiveChannels(this.liveFilter.page); this.fetchLiveStats(); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },

        /* ========== 分类管理 ========== */
        openCategoryModal() { this.showCategoryModal = true; },
        async addLiveCategory() {
            if (!this.newCategory.name.trim()) { this.showToast('请输入分类名称', 'error'); return; }
            try {
                await liveService.addCategory(this.newCategory);
                this.showToast('分类添加成功'); await this.fetchLiveCategories();
                const nextIdx = this.liveCategories.length > 0 ? Math.max(...this.liveCategories.map(c => c.sort_index)) + 1 : 0;
                this.newCategory = { name: '', sort_index: nextIdx, color: '#6366f1', is_visible: 1 };
                this.categoryTbodyKey++; this.$nextTick(() => { this.initCategorySortable(); });
            } catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async updateLiveCategory(cat) {
            try { await liveService.updateCategory(cat.id, cat); this.showToast('分类修改成功'); await this.fetchLiveCategories(); this.fetchLiveChannels(this.liveFilter.page); this.categoryTbodyKey++; this.$nextTick(() => { this.initCategorySortable(); }); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async deleteLiveCategory(id) {
            if (!confirm('确定要删除此分类吗？关联的频道将自动归入"未分类"。')) return;
            try { await liveService.deleteCategory(id); this.showToast('分类删除成功'); await this.fetchLiveCategories(); this.fetchLiveChannels(this.liveFilter.page); this.categoryTbodyKey++; this.$nextTick(() => { this.initCategorySortable(); }); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        initCategorySortable() {
            if (this.categorySortableInstance) this.categorySortableInstance.destroy();
            const el = document.getElementById('category-sortable-tbody');
            if (!el) return;
            const vm = this;
            this.categorySortableInstance = Sortable.create(el, {
                animation: 150, handle: '.drag-handle',
                onEnd: async function (evt) {
                    const items = Array.from(el.querySelectorAll('tr[data-cat-id]'));
                    const removed = items.splice(evt.oldIndex, 1)[0];
                    items.splice(evt.newIndex, 0, removed);
                    const order = items.map((item, i) => ({ id: parseInt(item.getAttribute('data-cat-id')), sort_index: i }));
                    const remainingItems = vm.liveCategories.filter(c => order.some(o => o.id === c.id));
                    remainingItems.forEach(item => { const o = order.find(o => o.id === item.id); if (o) item.sort_index = o.sort_index; });
                    vm.liveCategories = remainingItems; vm.categoryTbodyKey++; vm.$nextTick(() => { vm.initCategorySortable(); });
                    try { await liveService.reorderCategories({ order }); vm.showToast('分类排序已更新'); await vm.fetchLiveCategories(); vm.categoryTbodyKey++; vm.$nextTick(() => { vm.initCategorySortable(); }); }
                    catch (e) { vm.showToast('排序请求失败', 'error'); }
                }
            });
        },
        handleColorChange(item, color) { item.color = color; if (item.id && item.name !== undefined) { this.updateLiveCategory(item); } },

        /* ========== 导入 ========== */
        handleImportFileSelect(e) { const files = e.target.files; if (files && files.length > 0) this.importFile = files[0]; },
        handleImportFileDrop(e) {
            const files = e.dataTransfer.files;
            if (files && files.length > 0) {
                const file = files[0]; const ext = file.name.toLowerCase().split('.').pop();
                if (ext === 'm3u' || ext === 'm3u8') { this.importFile = file; }
                else { this.showToast('只支持导入 M3U 播放列表文件 (*.m3u / *.m3u8)', 'error'); }
            }
        },
        async importExternalChannels() {
            this.importingChannels = true;
            try {
                let res;
                if (this.importMethod === 'file') {
                    if (!this.importFile) { this.showToast('请先选择要上传的文件', 'error'); return; }
                    const fd = new FormData(); fd.append('file', this.importFile);
                    res = await liveService.importChannels(fd);
                    this.showToast(`导入成功！新增 ${res.new} 个，跳过 ${res.skipped} 个，总共 ${res.total} 个`);
                    this.importFile = null;
                } else {
                    if (!this.importText.trim()) { this.showToast('请粘贴文本内容再进行导入', 'error'); return; }
                    res = await liveService.importChannels({ content: this.importText });
                    this.showToast(`导入成功！新增 ${res.new} 个，跳过 ${res.skipped} 个，总共 ${res.total} 个`);
                    this.importText = '';
                }
                this.initLiveTab();
            } catch (e) { this.showToast(e.message || '导入过程中遭遇异常', 'error'); }
            finally { this.importingChannels = false; }
        },

        /* ========== 别名 ========== */
        openAliasModal() { this.showAliasModal = true; this.fetchAliases(); },
        quickAddAlias(sourceName) { this.newAlias.source_name = sourceName; this.newAlias.target_name = ''; this.showAliasModal = true; this.fetchAliases(); this.$nextTick(() => { const inputs = document.querySelectorAll('.category-add-inline input[type=text]'); if (inputs.length >= 2) inputs[1].focus(); }); },
        async fetchAliases() { try { this.aliases = await liveService.getAliases(); } catch (e) {} },
        async addAlias() {
            if (!this.newAlias.source_name.trim() || !this.newAlias.target_name.trim()) { this.showToast('原始名称和规范名称均不能为空', 'error'); return; }
            try { const res = await liveService.addAlias(this.newAlias); this.showToast('别名添加成功' + (res.affected_channels ? '，已更新 ' + res.affected_channels + ' 个频道' : '')); this.newAlias = { source_name: '', target_name: '' }; this.fetchAliases(); this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async saveAlias(a) {
            try { const res = await liveService.updateAlias(a.id, { source_name: a.source_name, target_name: a.target_name }); this.showToast('别名保存成功' + (res.affected_channels ? '，已更新 ' + res.affected_channels + ' 个频道' : '')); this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async deleteAlias(id) {
            if (!confirm('确定要删除这个别名映射吗？相关频道将恢复原始名称。')) return;
            try { await liveService.deleteAlias(id); this.showToast('别名删除成功，频道已恢复原始名称'); this.fetchAliases(); this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async reapplyAliases() {
            try { const res = await liveService.reapplyAliases(); this.showToast(`已重新应用：${res.applied} 条映射，${res.affected} 个频道`); this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '网络错误', 'error'); }
        },
        async exportAliases() {
            try { const data = await liveService.exportAliases(); const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `live_aliases_${new Date().toISOString().slice(0, 10)}.json`; a.click(); URL.revokeObjectURL(url); this.showToast(`已导出 ${data.count} 条别名映射`); }
            catch (e) { this.showToast('导出失败', 'error'); }
        },
        triggerAliasImport() { this.$refs.aliasFileInput.click(); },
        async handleAliasFileImport(e) {
            const file = e.target.files[0]; if (!file) return;
            try { const text = await file.text(); const data = JSON.parse(text); const res = await liveService.importAliases(data); this.showToast(`导入成功，共 ${res.imported} 条映射`); this.fetchAliases(); }
            catch (e) { this.showToast('文件解析失败，请检查 JSON 格式', 'error'); }
            e.target.value = '';
        },
        async exportCategoryMappings() {
            try { const data = await liveService.exportCategoryMappings(); const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `live_category_mappings_${new Date().toISOString().slice(0, 10)}.json`; a.click(); URL.revokeObjectURL(url); this.showToast(`已导出 ${data.count} 条频道-分类关系`); }
            catch (e) { this.showToast('导出关系失败', 'error'); }
        },
        triggerCategoryMappingImport() { this.$refs.categoryMappingFileInput.click(); },
        async handleCategoryMappingFileImport(e) {
            const file = e.target.files[0]; if (!file) return;
            try { const text = await file.text(); const data = JSON.parse(text); const res = await liveService.importCategoryMappings(data); this.showToast(`导入成功：关联了 ${res.imported_channels} 个频道的分类` + (res.created_categories ? `，自动创建了 ${res.created_categories} 个新分类` : '')); this.fetchLiveCategories(); this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '文件解析失败，请检查 JSON 格式', 'error'); }
            e.target.value = '';
        },

        /* ========== 批量操作 ========== */
        toggleSelectAll() { this.selectedChannelIds = this.selectAllChannels ? this.liveChannels.map(c => c.id) : []; },
        async batchSetEnabled(enabled) {
            if (!this.selectedChannelIds.length) return;
            try { const res = await liveService.batchEnabled(this.selectedChannelIds, enabled); this.showToast(res.message); this.selectedChannelIds = []; this.fetchLiveChannels(this.liveFilter.page); this.fetchLiveStats(); }
            catch (e) { this.showToast(e.message || '网络请求异常', 'error'); }
        },
        async batchChangeCategory(event) {
            const catId = parseInt(event.target.value);
            if (isNaN(catId) || !this.selectedChannelIds.length) return;
            try { const res = await liveService.batchCategory(this.selectedChannelIds, catId); this.showToast(res.message); this.selectedChannelIds = []; event.target.value = ''; this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast(e.message || '网络请求异常', 'error'); }
        },
        async batchDelete() {
            if (!this.selectedChannelIds.length) return;
            if (this.selectedChannelIds.some(id => { const ch = this.liveChannels.find(c => c.id === id); return ch && ch.source === 'server'; })) { this.showToast('服务器频道不能删除，请取消选中后重试', 'error'); return; }
            if (!confirm(`确定要删除选中的 ${this.selectedChannelIds.length} 个频道吗？`)) return;
            try { const res = await liveService.batchDelete(this.selectedChannelIds); this.showToast(res.message); this.selectedChannelIds = []; this.fetchLiveChannels(this.liveFilter.page); this.fetchLiveStats(); }
            catch (e) { this.showToast(e.message || '网络请求异常', 'error'); }
        },

        /* ========== 排序 ========== */
        async sortChannelsByCategory() {
            const catOrderMap = {}; this.liveCategories.forEach(cat => { catOrderMap[cat.id] = cat.sort_index || 0; });
            const sorted = [...this.liveChannels].sort((a, b) => { const oA = catOrderMap[a.category_id] !== undefined ? catOrderMap[a.category_id] : Number.MAX_SAFE_INTEGER; const oB = catOrderMap[b.category_id] !== undefined ? catOrderMap[b.category_id] : Number.MAX_SAFE_INTEGER; if (oA !== oB) return oA - oB; return (a.sort_index || 0) - (b.sort_index || 0); });
            const order = sorted.map((ch, i) => ({ id: ch.id, sort_index: i }));
            try { await liveService.reorderChannels({ order }); this.showToast('已按类别排序'); this.fetchLiveChannels(1); }
            catch (e) { this.showToast(e.message || '网络异常', 'error'); }
        },
        async resetLiveChannelsOrder() {
            if (!confirm('确定要恢复默认排序吗？')) return;
            try { const res = await liveService.resetOrder(); this.showToast(res.message); this.fetchLiveChannels(1); }
            catch (e) { this.showToast(e.message || '网络连接异常', 'error'); }
        },

        /* ========== 拖拽排序 ========== */
        initSortable() {
            if (this.sortableInstance) this.sortableInstance.destroy();
            const tbody = document.getElementById('live-channel-list-tbody');
            if (!tbody || this.liveChannels.length === 0) return;
            const vm = this;
            this.sortableInstance = Sortable.create(tbody, {
                animation: 150, handle: '.drag-handle', draggable: 'tr.live-channel-row',
                onEnd: async function (evt) {
                    const ids = Array.from(tbody.querySelectorAll('tr.live-channel-row')).map(tr => parseInt(tr.getAttribute('data-id')));
                    const order = ids.map((id, i) => ({ id, sort_index: i }));
                    vm.liveChannels.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
                    const source = evt.clone; const dragIds = source ? [parseInt(source.getAttribute('data-id'))] : [];
                    const isBatch = source && source.classList.contains('sortable-batch-ghost');
                    vm.tbodyKey++;
                    vm.$nextTick(async () => { vm.initSortable();
                        try { await liveService.reorderChannels({ order }); vm.showToast(isBatch ? `批量排序成功 (${dragIds.length} 个频道)` : '排序更新成功'); vm.fetchLiveStats(); }
                        catch (e) { vm.showToast('排序请求失败', 'error'); }
                    });
                }
            });
        },

        /* ========== 配置 ========== */
        openLiveConfigModal() { this.showLiveConfigModal = true; },
        async saveLiveConfig() {
            const payload = { ...this.liveConfig, udpxy_enabled: this.liveConfig.udpxy_enabled_bool ? '1' : '0', fcc_global_enabled: this.liveConfig.fcc_global_enabled_bool ? '1' : '0', timeshift_enabled: this.liveConfig.timeshift_enabled_bool ? '1' : '0', m3u_dual_line: this.liveConfig.m3u_dual_line_bool ? '1' : '0' };
            delete payload.udpxy_enabled_bool; delete payload.fcc_global_enabled_bool; delete payload.timeshift_enabled_bool; delete payload.m3u_dual_line_bool;
            try { await liveService.saveConfig(payload); this.showToast('直播配置保存成功'); this.showLiveConfigModal = false; this.fetchLiveChannels(this.liveFilter.page); }
            catch (e) { this.showToast('网络错误', 'error'); }
        },

        /* ========== Logo ========== */
        getLogoUrl(logo) {
            const base = this.liveConfig.logo_base_url || '';
            if (!logo) return '';
            if (/^https?:\/\//i.test(logo)) return logo;
            if (base) { const sep = base.endsWith('/') ? '' : '/'; return base + sep + logo; }
            return '/static/logo/' + logo;
        },
        handleLogoError(ch) {
            const logo = (ch.display_name || ch.name || '').trim();
            if (logo) {
                const is4k = /(?:\s*|-|_)?(?:4[kK]|8[kK])/;
                if (is4k.test(logo)) {
                    const clean = logo.replace(is4k, '');
                    const cleanName = clean + '.png';
                    if (clean && logo !== clean && cleanName !== ch.logo_url) {
                        ch.logo_url = cleanName;
                        return;
                    }
                }
            }
            ch.logo_failed = true;
        },

        /* ========== EPG 节目预览 ========== */
        openEpgPreview(ch) {
            this.epgPreviewChannel = ch;
            this.showEpgPreviewModal = true;
            this.epgPrograms = [];
            const dates = [];
            const backTime = 7;
            const now = new Date();
            for (let i = -backTime; i <= 1; i++) {
                const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
                dates.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
            }
            this.epgPreviewDates = dates;
            this.epgPreviewDateIndex = backTime;
            this.fetchEpgPrograms();
        },

        async fetchEpgPrograms() {
            if (!this.epgPreviewChannel) return;
            this.epgLoading = true;
            this.epgPrograms = [];
            try {
                const channelId = this.epgPreviewChannel.tvg_id || this.epgPreviewChannel.channel_id || this.epgPreviewChannel.name;
                const data = await epgService.getPrograms(channelId, this.epgPreviewDates[this.epgPreviewDateIndex]);
                this.epgPrograms = data.items || [];
                this.$nextTick(() => this.scrollToPlayingProgram());
            } catch (e) {
                console.error('加载节目单失败:', e);
            } finally { this.epgLoading = false; }
        },

        prevEpgDay() { if (this.epgPreviewDateIndex > 0) { this.epgPreviewDateIndex--; this.fetchEpgPrograms(); } },
        nextEpgDay() { if (this.epgPreviewDateIndex < this.epgPreviewDates.length - 1) { this.epgPreviewDateIndex++; this.fetchEpgPrograms(); } },

        isProgramPlaying(prog) {
            if (!prog.start_time || !prog.end_time) return false;
            try {
                const parse = (str) => { const [ymd, hms] = str.split(' '); const [y, m, d] = ymd.split('-'); const [hh, mm, ss] = hms.split(':'); return new Date(y, m - 1, d, hh, mm, ss); };
                const now = new Date();
                return now >= parse(prog.start_time) && now <= parse(prog.end_time);
            } catch (e) { return false; }
        },

        formatProgTimeRange(prog) {
            if (!prog.start_time || !prog.end_time) return '';
            try { const h = (s) => s.split(' ')[1].split(':').slice(0, 2).join(':'); return `${h(prog.start_time)} - ${h(prog.end_time)}`; } catch (e) { return ''; }
        },

        formatEpgDateLabel(dateStr) {
            if (!dateStr) return '';
            try {
                const parts = dateStr.split('-');
                const target = new Date(parts[0], parts[1] - 1, parts[2]);
                const today = new Date(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
                const diff = Math.round((target - today) / 86400000);
                let rel;
                if (diff === 0) rel = '今天';
                else if (diff === -1) rel = '昨天';
                else if (diff === 1) rel = '明天';
                else rel = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][target.getDay()];
                return `${parts[1]}月${parts[2]}日 (${rel})`;
            } catch (e) { return dateStr; }
        },

        scrollToPlayingProgram() {
            setTimeout(() => {
                const c = this.$refs.epgProgramList;
                const rows = this.$refs.epgRows;
                if (!c || !rows || rows.length === 0) return;
                const el = rows.find((r, i) => this.epgPrograms[i] && this.isProgramPlaying(this.epgPrograms[i]));
                if (el) {
                    const ch = c.clientHeight, cr = c.getBoundingClientRect(), er = el.getBoundingClientRect();
                    c.scrollTop = (er.top - cr.top + c.scrollTop) - ch / 2 + (er.height || el.clientHeight) / 2;
                }
            }, 150);
        },
    },

    template: /* html */`
        <div class="live-top-panel">
            <div class="card import-card">
                <div class="card-header"><h3>导入外部频道</h3></div>
                <div class="import-panel-content">
                    <div class="import-options">
                        <div class="import-tabs">
                            <button :class="['import-tab-btn', { active: importMethod === 'text' }]" @click="importMethod = 'text'">🔗 URL / 文本</button>
                            <button :class="['import-tab-btn', { active: importMethod === 'file' }]" @click="importMethod = 'file'">📁 上传文件</button>
                        </div>
                        <div class="form-actions mt-15"><button class="btn btn-primary w-full" @click="importExternalChannels" :disabled="importingChannels"><span class="spinner" v-if="importingChannels"></span>{{ importingChannels ? '导入中...' : '📥 开始导入' }}</button></div>
                    </div>
                    <div class="import-area">
                        <div v-if="importMethod === 'text'" class="w-full"><textarea id="import-text" name="m3u_text" v-model="importText" placeholder="在此粘贴 M3U 订阅链接或 M3U 文本内容..." rows="4" class="import-textarea"></textarea></div>
                        <div v-else class="w-full">
                            <div class="file-upload-zone" @dragover.prevent @drop.prevent="handleImportFileDrop">
                                <span class="upload-icon">📥</span>
                                <p v-if="!importFile">拖拽文件到此处，或 <label class="file-label" for="import-file">选择文件</label><input type="file" id="import-file" name="m3u_file" @change="handleImportFileSelect" accept=".m3u,.m3u8" class="hidden"></p>
                                <p v-else class="text-success text-xs">已选择文件: <strong>{{ importFile.name }}</strong> <button class="btn-clear" @click="importFile = null">[清除]</button></p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="card stats-card">
                <div class="card-header"><h3>统计信息</h3></div>
                <div class="status-list horizontal-stats">
                    <div class="status-item"><span class="status-label">服务器频道数</span><span class="status-val highlight">{{ liveStats.server }}</span></div>
                    <div class="status-item"><span class="status-label">外部导入数</span><span class="status-val highlight">{{ liveStats.external }}</span></div>
                    <div class="status-item"><span class="status-label">已启用</span><span class="status-val text-success">{{ liveStats.enabled }}</span></div>
                    <div class="status-item"><span class="status-label">已禁用</span><span class="status-val text-error">{{ liveStats.disabled }}</span></div>
                </div>
            </div>
        </div>
        <div class="live-toolbar">
            <div class="toolbar-left">
                <div class="filter-group"><select name="filter_category" v-model="liveFilter.category_id" @change="fetchLiveChannels(1)"><option :value="null">全部分类</option><option :value="0">未分类</option><option v-for="cat in liveCategories" :key="cat.id" :value="cat.id">{{ cat.name }}</option></select></div>
                <div class="filter-group"><select name="filter_enabled" v-model="liveFilter.enabled" @change="fetchLiveChannels(1)"><option :value="null">全部状态</option><option :value="1">仅启用</option><option :value="0">仅禁用</option></select></div>
                <div class="filter-group"><select name="filter_source" v-model="liveFilter.source" @change="fetchLiveChannels(1)"><option :value="null">全部来源</option><option value="server">服务器下发</option><option value="external">外部导入</option></select></div>
                <div class="filter-group search-group"><input type="text" id="live-search" name="keyword" v-model="liveFilter.keyword" placeholder="搜索频道名 / ID / 序号" @keyup.enter="fetchLiveChannels(1)"><button class="btn btn-secondary btn-sm" @click="fetchLiveChannels(1)">🔍</button></div>
            </div>
            <div class="toolbar-right">
                <button class="btn btn-secondary" @click="openCategoryModal">📂 分类管理</button>
                <button class="btn btn-secondary" @click="openAliasModal">🔗 别名映射</button>
                <button class="btn btn-secondary" @click="openLiveConfigModal">⚙️ 直播设置</button>
                <button class="btn btn-secondary" @click="sortChannelsByCategory">📑 按类别排序</button>
                <button class="btn btn-secondary" @click="resetLiveChannelsOrder">🧹 恢复默认排序</button>
                <button class="btn btn-primary" @click="triggerLiveSync" :disabled="syncingLive || !simStatus.is_authenticated"><span class="spinner" v-if="syncingLive"></span>{{ syncingLive ? '🔄 同步中...' : '🔄 同步频道' }}</button>
            </div>
        </div>
        <div class="live-bottom-panel">
            <div class="card live-list-card">
                <div class="card-header"><h3>直播频道列表</h3><span class="badge" v-if="liveTotal">共 {{ liveTotal }} 个</span></div>
                <div class="batch-actions-bar" v-if="selectedChannelIds.length > 0">
                    <span class="selected-count">已选中 <strong>{{ selectedChannelIds.length }}</strong> 个频道</span>
                    <div class="batch-buttons">
                        <button class="btn btn-secondary btn-sm" @click="batchSetEnabled(1)">🟢 批量启用</button>
                        <button class="btn btn-secondary btn-sm" @click="batchSetEnabled(0)">🔴 批量禁用</button>
                        <select name="batch_category" class="batch-cat-select" @change="batchChangeCategory($event)"><option value="" disabled selected>📂 批量修改分类...</option><option value="0">其他 (未分类)</option><option v-for="cat in liveCategories" :key="cat.id" :value="cat.id">{{ cat.name }}</option></select>
                        <button class="btn btn-danger btn-sm" @click="batchDelete" :disabled="!canBatchDelete">🗑️ 批量删除</button>
                    </div>
                </div>
                <div class="table-container"><table class="live-table"><thead><tr>
                    <th class="cell-sm"><input type="checkbox" id="select-all" name="select_all" v-model="selectAllChannels" @change="toggleSelectAll"></th><th class="cell-sm">启用</th><th class="cell-sm">排序</th><th class="w-100">序号 / ID</th><th class="cell-sm">台标</th><th class="w-150">原名</th><th class="w-200">别名</th><th class="w-90">所属分类</th><th>组播地址</th><th>单播地址</th><th class="w-120 ta-center">操作</th>
                </tr></thead>
                <tbody id="live-channel-list-tbody" :key="tbodyKey">
                    <tr v-if="liveChannels.length === 0"><td colspan="11" class="empty-row pad-30">暂无满足条件的频道数据，请尝试同步或手动导入</td></tr>
                    <tr v-for="ch in liveChannels" :key="ch.id" :data-id="ch.id" class="live-channel-row">
                        <td class="ta-center"><input type="checkbox" :name="'ch_select_' + ch.id" :value="ch.id" v-model="selectedChannelIds"></td>
                        <td class="ta-center"><label class="switch-toggle"><input type="checkbox" :name="'ch_enabled_' + ch.id" :checked="ch.is_enabled === 1" @change="toggleChannelEnabled(ch)"><span class="switch-slider"></span></label></td>
                        <td class="drag-handle ta-center"><span class="drag-grip">☰</span></td>
                        <td class="nowrap"><span class="text-secondary fw-600" v-if="ch.user_channel_id">{{ ch.user_channel_id }}</span><span class="text-muted hint-sm" v-if="ch.channel_id">(ID: {{ ch.channel_id }})</span></td>
                        <td class="ta-center pad-4"><img v-if="ch.logo_url && !ch.logo_failed" :src="getLogoUrl(ch.logo_url)" alt="logo" class="channel-logo" @error="handleLogoError(ch)"><span v-else class="fs-14-muted">📺</span></td>
                        <td class="nowrap"><span class="channel-name-cell clickable" :title="'点击添加别名映射: ' + ch.name" @click="quickAddAlias(ch.name)">{{ ch.name }}</span></td>
                        <td class="nowrap"><span class="channel-name-cell">{{ ch.display_name || ch.name }}</span><span v-if="ch.source === 'external'" class="channel-tag tag-primary">外部</span><span v-if="ch.timeshift_enabled === 1" class="channel-tag tag-green" :title="'支持时移回看，回看时长共 ' + (ch.back_time || 0) + ' 天'">回看: {{ ch.back_time || 0 }}天</span><span v-if="ch.epg_days && ch.epg_days > 0" class="channel-tag tag-blue" :title="'点击预览 EPG 节目单 (当前共 ' + ch.epg_days + ' 天)'" @click="openEpgPreview(ch)">EPG: {{ ch.epg_days }}天</span><span v-else class="channel-tag tag-slate" title="暂无可用 EPG 数据">无EPG</span></td>
                        <td><select :name="'ch_cat_' + ch.id" class="cat-select" v-model="ch.category_id" @change="changeChannelCategory(ch)" :style="{ borderLeft: '4px solid ' + (ch.category_color || 'var(--border-color)') }"><option :value="0">未分类</option><option v-for="cat in liveCategories" :key="cat.id" :value="cat.id">{{ cat.name }}</option></select></td>
                        <td class="url-cell" :title="ch.multicast_url"><code class="url-text">{{ ch.multicast_url || '—' }}</code></td>
                        <td class="url-cell" :title="ch.unicast_url"><code class="url-text">{{ ch.unicast_url || '—' }}</code></td>
                        <td class="ta-center"><div class="row-actions justify-center"><button v-if="ch.source === 'external'" class="btn-text btn-text-danger" @click="deleteChannel(ch.id)" title="删除外部频道">🗑️</button></div></td>
                    </tr>
                </tbody></table></div>
            </div>
        </div>

        <!-- EPG 节目单预览弹窗 -->
        <transition name="fade">
            <div class="modal-overlay" v-if="showEpgPreviewModal" @click.self="showEpgPreviewModal = false">
                <div class="modal-card epg-preview-card w-modal-narrow">
                    <div class="modal-header">
                        <div class="flex-ac-g8">
                            <h3>📺 {{ epgPreviewChannel ? (epgPreviewChannel.display_name || epgPreviewChannel.name) : 'EPG 节目单预览' }}</h3>
                            <span class="badge fs-11">EPG ID: {{ epgPreviewChannel.tvg_id }}</span>
                        </div>
                        <button class="modal-close" @click="showEpgPreviewModal = false">×</button>
                    </div>
                    <div class="modal-body col-stack">
                        <div class="epg-date-nav">
                            <button class="btn btn-secondary btn-xs" :disabled="epgPreviewDateIndex === 0" @click="prevEpgDay">◀</button>
                            <span class="fs-14-fw600">{{ epgFormattedDate }}</span>
                            <button class="btn btn-secondary btn-xs" :disabled="epgPreviewDateIndex === epgPreviewDates.length - 1" @click="nextEpgDay">▶</button>
                        </div>
                        <div class="epg-program-list" ref="epgProgramList">
                            <div v-if="epgLoading" class="empty-state">
                                <span class="spinner mr-8-vmid"></span>
                                正在加载节目单数据...
                            </div>
                            <div v-else-if="epgPrograms.length === 0" class="empty-state">
                                暂无该日期的节目单数据 📺
                            </div>
                            <div v-else>
                                <div v-for="prog in epgPrograms" :key="prog.id"
                                     :class="['epg-prog-row', { 'is-playing': isProgramPlaying(prog) }]"
                                     ref="epgRows">
                                    <div class="epg-prog-inner">
                                        <div class="epg-prog-time-col">
                                            <span class="epg-time">{{ formatProgTimeRange(prog) }}</span>
                                            <span v-if="isProgramPlaying(prog)" class="epg-badge-playing" title="当前正在直播的节目">🟢 正在播放</span>
                                        </div>
                                        <span class="epg-prog-title">{{ prog.title }}</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </transition>

        <!-- 直播频道设置弹窗 -->
        <transition name="fade">
            <div class="modal-overlay" v-if="showLiveConfigModal" @click.self="showLiveConfigModal = false">
                <div class="modal-card">
                    <div class="modal-header">
                        <h3>直播频道配置</h3>
                        <button class="modal-close" @click="showLiveConfigModal = false">×</button>
                    </div>
                    <div class="modal-body">
                        <div class="form-group inline-checkbox-group mt-10 mb-15">
                            <div class="switch-item">
                                <label class="switch-toggle">
                                    <input type="checkbox" v-model="liveConfig.udpxy_enabled_bool">
                                    <span class="switch-slider"></span>
                                </label>
                                <span class="switch-label-text">🔗 启用 UDPXY 组播代理模式</span>
                            </div>
                            <small class="form-help text-muted">开启后，igmp:// 组播头会被自动转换成 udpxy 的 http:// 代理格式；关闭则保持原始 igmp:// 连接。</small>
                        </div>
                        <div class="form-group inline-checkbox-group mb-15-fade" :style="{ opacity: liveConfig.udpxy_enabled_bool ? 1 : 0.5, pointerEvents: liveConfig.udpxy_enabled_bool ? 'auto' : 'none' }">
                            <div class="switch-item">
                                <label class="switch-toggle">
                                    <input type="checkbox" v-model="liveConfig.fcc_global_enabled_bool" :disabled="!liveConfig.udpxy_enabled_bool">
                                    <span class="switch-slider"></span>
                                </label>
                                <span class="switch-label-text">⚡ 启用全局 FCC 加速</span>
                            </div>
                            <small class="form-help text-muted">开启后，若频道支持 FCC，则在 udpxy 转换链接后追加 ?fcc= 提速参数。仅在 UDPXY 模式开启时有效。</small>
                        </div>
                        <div class="form-group inline-checkbox-group mb-15">
                            <div class="switch-item">
                                <label class="switch-toggle">
                                    <input type="checkbox" v-model="liveConfig.timeshift_enabled_bool">
                                    <span class="switch-slider"></span>
                                </label>
                                <span class="switch-label-text">🕒 启用全局时移回看 (Catchup)</span>
                            </div>
                            <small class="form-help text-muted">开启后，对支持时移的频道生成 catchup="default" 时移元数据。</small>
                        </div>
                        <div class="form-group inline-checkbox-group mb-20">
                            <div class="switch-item">
                                <label class="switch-toggle">
                                    <input type="checkbox" v-model="liveConfig.m3u_dual_line_bool">
                                    <span class="switch-slider"></span>
                                </label>
                                <span class="switch-label-text">🛤️ 启用组播+单播双线模式</span>
                            </div>
                            <small class="form-help text-muted">开启后，同一频道将同时生成 udpxy 组播和 RTSP 单播两行数据。</small>
                        </div>
                        <div class="form-group mb-15-fade" :style="{ opacity: liveConfig.udpxy_enabled_bool ? 1 : 0.5, pointerEvents: liveConfig.udpxy_enabled_bool ? 'auto' : 'none' }">
                            <label for="live-udpxy">udpxy 代理服务地址</label>
                            <input type="text" id="live-udpxy" v-model="liveConfig.udpxy_address" placeholder="例如: http://192.168.1.1:6688" :disabled="!liveConfig.udpxy_enabled_bool">
                            <small class="form-help">用于转换 igmp:// 组播到 http:// 代理地址。若为空则保持原始组播输出。</small>
                        </div>
                        <div class="form-group mb-15">
                            <label for="live-logo-url">LOGO 基础 URL</label>
                            <input type="text" id="live-logo-url" v-model="liveConfig.logo_base_url" placeholder="默认为 /static/logo/">
                            <small class="form-help">用于拼接频道台标地址。若是相对路径，生成 M3U 时会自动拼接当前的网卡 Host 头部。</small>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary" @click="showLiveConfigModal = false">取消</button>
                        <button class="btn btn-primary" @click="saveLiveConfig">保存设置</button>
                    </div>
                </div>
            </div>
        </transition>

        <!-- 分类管理弹窗 -->
        <transition name="fade">
            <div class="modal-overlay" v-if="showCategoryModal" @click.self="showCategoryModal = false">
                <div class="modal-card modal-large">
                    <div class="modal-header">
                        <h3>分类频道管理</h3>
                        <button class="modal-close" @click="showCategoryModal = false">×</button>
                    </div>
                    <div class="modal-body">
                        <div class="category-add-inline">
                            <input type="text" v-model="newCategory.name" placeholder="分类名称" class="input-sm flex-1">
                            <color-picker :color="newCategory.color" @select="newCategory.color = $event"></color-picker>
                            <button class="btn btn-primary btn-sm" @click="addLiveCategory">＋ 添加</button>
                        </div>
                        <div class="category-list-container">
                            <table class="live-table table-sm">
                                <thead>
                                    <tr>
                                        <th class="w-40 ta-center">排序</th>
                                        <th>分类名称</th>
                                        <th class="w-100">排序索引</th>
                                        <th class="w-80 ta-center">标色</th>
                                        <th class="w-120 ta-center">操作</th>
                                    </tr>
                                </thead>
                                <tbody id="category-sortable-tbody" :key="categoryTbodyKey">
                                    <tr v-for="cat in liveCategories" :key="cat.id" :data-cat-id="cat.id">
                                        <td class="drag-handle ta-center w-40 cursor-grab">
                                            <span class="fs-12-muted">⋮⋮</span>
                                        </td>
                                        <td><input type="text" v-model="cat.name" class="input-table-cell"></td>
                                        <td class="ta-center"><span>{{ cat.sort_index }}</span></td>
                                        <td class="ta-center">
                                            <color-picker :color="cat.color" @select="cat.color = $event; updateLiveCategory(cat)"></color-picker>
                                        </td>
                                        <td class="ta-center">
                                            <div class="row-actions justify-center">
                                                <button class="btn-text btn-text-primary" @click="updateLiveCategory(cat)">保存</button>
                                                <button class="btn-text btn-text-danger" @click="deleteLiveCategory(cat.id)">删除</button>
                                            </div>
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                        <div class="divider-top">
                            <div class="row-actions row-actions-start">
                                <button class="btn btn-secondary btn-sm" @click="exportCategoryMappings">📤 导出"频道-分类"关系</button>
                                <button class="btn btn-secondary btn-sm" @click="triggerCategoryMappingImport">📥 导入"频道-分类"关系</button>
                            </div>
                            <small class="form-help mt-4">导出或导入频道与分类的对应分配关系。导入时若对应分类不存在，系统将自动创建该分类，并自动关联频道。</small>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-primary" @click="showCategoryModal = false">关闭</button>
                    </div>
                </div>
            </div>
        </transition>

        <!-- 频道属性编辑弹窗 -->
        <transition name="fade">
            <div class="modal-overlay" v-if="showEditChannelModal" @click.self="showEditChannelModal = false">
                <div class="modal-card">
                    <div class="modal-header">
                        <h3>编辑直播频道</h3>
                        <button class="modal-close" @click="showEditChannelModal = false">×</button>
                    </div>
                    <div class="modal-body" v-if="editingCh">
                        <div class="form-group">
                            <label>原名<span class="text-muted hint-xs">（只读）</span></label>
                            <input type="text" :value="editingCh.name" disabled class="input-disabled">
                        </div>
                        <div class="form-group">
                            <label>别名<span class="text-muted hint-xs">（只读，由别名映射控制）</span></label>
                            <input type="text" :value="editingCh.display_name || editingCh.name" disabled class="input-disabled">
                            <small v-if="editingCh.source === 'server'" class="form-help">在频道列表中点击原名打开别名映射。</small>
                        </div>
                        <div class="form-group">
                            <label>所属分类</label>
                            <select name="edit_category" v-model="editingCh.category_id">
                                <option :value="0">未分类</option>
                                <option v-for="cat in liveCategories" :key="cat.id" :value="cat.id">{{ cat.name }}</option>
                            </select>
                        </div>
                        <template v-if="editingCh.source === 'server'">
                            <div class="form-group">
                                <label>Logo 文件<span class="text-muted hint-xs">（自动归一化生成）</span></label>
                                <input type="text" :value="editingCh.logo_url" disabled class="input-disabled">
                            </div>
                            <div class="form-group">
                                <label>tvg-id / tvg-name<span class="text-muted hint-xs">（自动归一化生成）</span></label>
                                <input type="text" :value="editingCh.tvg_id" disabled class="input-disabled input-half">
                                <input type="text" :value="editingCh.tvg_name" disabled class="input-disabled input-half mt-4">
                            </div>
                        </template>
                        <template v-if="editingCh.source === 'external'">
                            <div class="form-group"><label>频道名称</label><input type="text" v-model="editingCh.name"></div>
                            <div class="form-group"><label>显示名称</label><input type="text" v-model="editingCh.display_name"></div>
                            <div class="form-group"><label>Logo 文件名</label><input type="text" v-model="editingCh.logo_url"></div>
                            <div class="form-group"><label>EPG 匹配 ID (tvg-id)</label><input type="text" v-model="editingCh.tvg_id"></div>
                            <div class="form-group"><label>EPG 匹配名称 (tvg-name)</label><input type="text" v-model="editingCh.tvg_name"></div>
                            <div class="form-group"><label>组播地址 (igmp://)</label><input type="text" v-model="editingCh.multicast_url"></div>
                            <div class="form-group"><label>单播地址 (rtsp://)</label><input type="text" v-model="editingCh.unicast_url"></div>
                            <div class="form-group"><label>频道 ID (channel_id)</label><input type="text" v-model="editingCh.channel_id"></div>
                            <div class="form-group"><label>频道序号 (user_channel_id)</label><input type="text" v-model="editingCh.user_channel_id"></div>
                        </template>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary" @click="showEditChannelModal = false">取消</button>
                        <button class="btn btn-primary" @click="saveChannelEdit">保存修改</button>
                    </div>
                </div>
            </div>
        </transition>

        <!-- 别名映射管理弹窗 -->
        <transition name="fade">
            <div class="modal-overlay" v-if="showAliasModal" @click.self="showAliasModal = false">
                <div class="modal-card modal-large">
                    <div class="modal-header">
                        <h3>频道别名映射表</h3>
                        <button class="modal-close" @click="showAliasModal = false">×</button>
                    </div>
                    <div class="modal-body">
                        <p class="form-help mb-12">将服务器下发的乱名映射为规范名称。例如：<code>中央音乐高清</code> → <code>CCTV15音乐高清</code></p>
                        <div class="category-add-inline">
                            <input type="text" v-model="newAlias.source_name" placeholder="原始名称" class="input-sm flex-1">
                            <span class="pad-x8-muted">→</span>
                            <input type="text" v-model="newAlias.target_name" placeholder="规范名称" class="input-sm flex-1">
                            <button class="btn btn-primary btn-sm" @click="addAlias">＋ 添加</button>
                        </div>
                        <div class="category-list-container">
                            <table class="live-table table-sm">
                                <thead><tr><th class="w-50p">原始名称 (source)</th><th class="w-50p">规范名称 (target)</th><th class="w-100 ta-center">操作</th></tr></thead>
                                <tbody>
                                    <tr v-if="aliases.length === 0"><td colspan="3" class="empty-row pad-20">暂无别名映射，添加一条试试</td></tr>
                                    <tr v-for="a in aliases" :key="a.id">
                                        <td><input type="text" v-model="a.source_name" class="input-table-cell"></td>
                                        <td><input type="text" v-model="a.target_name" class="input-table-cell"></td>
                                        <td class="ta-center">
                                            <div class="row-actions justify-center">
                                                <button class="btn-text btn-text-primary" @click="saveAlias(a)">保存</button>
                                                <button class="btn-text btn-text-danger" @click="deleteAlias(a.id)">删除</button>
                                            </div>
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                        <div class="divider-top">
                            <div class="row-actions row-actions-start">
                                <button class="btn btn-secondary btn-sm" @click="exportAliases">📤 导出 JSON</button>
                                <button class="btn btn-secondary btn-sm" @click="triggerAliasImport">📥 导入 JSON</button>
                                <button class="btn btn-warning btn-sm" @click="reapplyAliases">🔄 重新应用到频道</button>
                            </div>
                            <small class="form-help mt-4">修改别名后自动应用到已有频道。换设备部署时，导出 JSON 备份，导入即可恢复。</small>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-primary" @click="showAliasModal = false">关闭</button>
                    </div>
                </div>
            </div>
        </transition>

        <!-- 文件导入 input -->
        <input type="file" id="import-aliases" name="aliases_file" ref="aliasFileInput" accept=".json" @change="handleAliasFileImport" class="hidden">
        <input type="file" id="import-category-mapping" name="category_mapping_file" ref="categoryMappingFileInput" accept=".json" @change="handleCategoryMappingFileImport" class="hidden">
    `
};
