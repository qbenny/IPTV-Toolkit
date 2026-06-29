// Default configuration templates
const DEFAULT_MOVIES_FILTERS = [
    {
        key: "sub_type",
        name: "分类",
        value: [
            { n: "全部电影", v: "" },
            { n: "4K大片", v: "category_82023212" },
            { n: "院线首映", v: "category_96791097" },
            { n: "好莱坞巨制", v: "category_80454289" },
            { n: "按地区", v: "category_00823570" },
            { n: "系列电影", v: "category_26400459" },
            { n: "焦点影人", v: "category_45105530" },
            { n: "重磅推荐", v: "category_18147166" }
        ]
    }
];

const DEFAULT_SERIES_FILTERS = [
    {
        key: "sub_type",
        name: "分类",
        value: [
            { n: "全部剧集", v: "" },
            { n: "最新上线", v: "category_36870794" },
            { n: "卫视同步", v: "category_92376156" },
            { n: "高分好剧", v: "category_53224763" },
            { n: "1080", v: "category_18008093" },
            { n: "古装", v: "category_82205358" },
            { n: "谍战", v: "category_86585701" },
            { n: "偶像", v: "category_33770041" },
            { n: "都市", v: "category_42484986" },
            { n: "TVB", v: "category_52215058" },
            { n: "年代", v: "category_88074906" },
            { n: "罪案", v: "category_94892066" },
            { n: "亚洲", v: "category_54399439" },
            { n: "欧美", v: "category_88274503" },
            { n: "其它", v: "category_77510266" }
        ]
    }
];

const { createApp } = Vue;

const app = createApp({
    data() {
        return {
            activeTab: 'stb', // Current visible main tab: 'stb', 'vod', 'live'
            theme: 'dark',    // 'dark' or 'light'
            
            // Toast Notification State
            toast: {
                show: false,
                message: '',
                type: 'success',
                timeoutId: null
            },
            
            // UI text mappings
            tabTitles: {
                stb: '系统凭证配置',
                vod: 'VOD 分类映射',
                live: '直播 & EPG'
            },
            tabSubtitles: {
                stb: '配置电信机顶盒仿真认证参数，保障安全接入 EPG 网关',
                vod: '映射电信原始 VOD 分类到自定义平铺过滤器，支持拖拽和重命名',
                live: '配置直播源和节目单抓取计划（暂未开放）'
            },
            
            // Plate 1: Credentials
            stbConfig: {
                user_id: '',
                stb_id: '',
                mac_address: '',
                base_url: '',
                des_key: '',
                ip_address: ''
            },
            resolvedIp: '',
            savingStb: false,
            
            // Sim Authentication Status
            simStatus: {
                is_authenticated: false,
                epg_base_url: null,
                user_token: null,
                jsessionid: null,
            },
            simStatusTimer: null,
            
            // Log Display State
            logs: [],
            logLevelFilter: 'ALL',
            logAutoScroll: true,
            logPollTimer: null,
            
            // Plate 2: Dynamic Category Map (CMS)
            vodCategories: [],       // List of customizable dynamic categories
            activeCategoryId: '',    // Currently selected category tab ID
            editingCatId: null,      // ID of category tab currently being inline-renamed
            treeSearchQuery: '',
            sourceTree: [],
            expandedNodes: [],       // List of expanded category node IDs in tree
            editingIdx: null,
            savingFilters: false,
            sortableInstance: null,
            tabsSortableInstance: null,
            sourceTreeLoading: false,   // Initial load spinner
            sourceTreeRefreshing: false, // Background refresh in progress
            sourceTreeMeta: { last_updated: null, cached: false, done: 0, total: 0 },
            _sourceTreePollTimer: null,
            
            // Custom Modals State (only used for delete confirmation)
            modal: {
                show: false,
                type: 'delete',
                title: '',
                value: '',
                targetCat: null,
                targetIdx: null
            }
        };
    },
    
    computed: {
        filteredLogs() {
            if (this.logLevelFilter === 'ALL') {
                return this.logs;
            }
            const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
            const minIdx = levels.indexOf(this.logLevelFilter);
            return this.logs.filter(log => {
                const idx = levels.indexOf(log.level);
                return idx >= minIdx;
            });
        },

        // Flat list active for current active sub-category filters
        activeFilterList() {
            const currentCat = this.vodCategories.find(c => c.id === this.activeCategoryId);
            if (currentCat && currentCat.filters && currentCat.filters[0]) {
                return currentCat.filters[0].value;
            }
            return [];
        },
        
        // Filter VOD catalog tree in real-time based on query
        filteredSourceTree() {
            if (!this.treeSearchQuery.trim()) {
                return this.sourceTree;
            }
            
            const query = this.treeSearchQuery.toLowerCase();
            
            // Deep clone to prevent mutating base tree
            const cloneTree = JSON.parse(JSON.stringify(this.sourceTree));
            
            const filterNode = (node) => {
                const matchName = node.name.toLowerCase().includes(query) || (node.id && node.id.toLowerCase().includes(query));
                
                if (node.children && node.children.length > 0) {
                    node.children = node.children.filter(filterNode);
                    // Keep this node if it matches OR any of its children matches
                    if (node.children.length > 0) {
                        // Automatically expand matching parent nodes
                        if (!this.expandedNodes.includes(node.id)) {
                            this.expandedNodes.push(node.id);
                        }
                        return true;
                    }
                }
                
                return matchName;
            };
            
            return cloneTree.filter(filterNode);
        },

        // Generate VOD API links based on current page origin
        vodApiLinks() {
            const origin = window.location.origin;
            return {
                tvbox: `${origin}/zjvod`,
                api: `${origin}/api/vod`
            };
        }
    },
    
    watch: {
        // Whenever active category changes, re-initialize SortableJS on right pane
        activeCategoryId() {
            this.editingIdx = null;
            this.$nextTick(() => {
                this.initSortable();
            });
        },
        
        // Re-init Sortable when changing active main tab
        activeTab(newTab) {
            if (newTab === 'vod') {
                this.$nextTick(() => {
                    this.initSortable();
                    this.initTabsSortable();
                });
            }
            if (newTab === 'stb') {
                this.startLogPolling();
                this.startSimStatusPolling();
            } else {
                this.stopLogPolling();
                this.stopSimStatusPolling();
            }
        }
    },
    
    created() {
        this.initTheme();
        this.fetchStbConfig();
        this.fetchVodCategories();
        this.fetchSourceTree();
        if (this.activeTab === 'stb') {
            this.startLogPolling();
            this.startSimStatusPolling();
        }
    },
    
    mounted() {
        this.$nextTick(() => {
            this.initSortable();
            this.initTabsSortable();
        });
    },

    beforeUnmount() {
        this.stopLogPolling();
        this.stopSimStatusPolling();
    },
    
    methods: {
        // Theme Switch Management
        initTheme() {
            const savedTheme = localStorage.getItem('theme') || 'dark';
            this.theme = savedTheme;
            document.documentElement.setAttribute('data-theme', savedTheme);
        },
        
        toggleTheme() {
            const newTheme = this.theme === 'dark' ? 'light' : 'dark';
            this.theme = newTheme;
            localStorage.setItem('theme', newTheme);
            document.documentElement.setAttribute('data-theme', newTheme);
            this.showToast(`主题已切换为 ${newTheme === 'dark' ? '深色' : '浅色'} 模式`);
        },
        
        // Sim Status Polling
        async fetchSimStatus() {
            try {
                const response = await fetch('/api/sim-status');
                if (response.ok) {
                    this.simStatus = await response.json();
                }
            } catch (error) {
                // Silent fail - don't spam errors in console
            }
        },
        
        startSimStatusPolling() {
            this.fetchSimStatus();
            if (!this.simStatusTimer) {
                this.simStatusTimer = setInterval(() => this.fetchSimStatus(), 5000);
            }
        },
        
        stopSimStatusPolling() {
            if (this.simStatusTimer) {
                clearInterval(this.simStatusTimer);
                this.simStatusTimer = null;
            }
        },
        
        maskToken(token) {
            if (!token || token.length <= 12) return token;
            return token.substring(0, 6) + '••••••••' + token.substring(token.length - 6);
        },
        
        async copyToClipboard(text) {
            try {
                // 优先使用现代 Clipboard API（HTTPS / localhost 环境）
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(text);
                    this.showToast('已复制到剪贴板 ✓', 'success');
                    return;
                }
                // 降级方案：使用 execCommand（兼容 HTTP 环境）
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                const success = document.execCommand('copy');
                document.body.removeChild(textarea);
                if (success) {
                    this.showToast('已复制到剪贴板 ✓', 'success');
                } else {
                    this.showToast('复制失败，请手动复制', 'error');
                }
            } catch (e) {
                this.showToast('复制失败，请手动复制', 'error');
            }
        },
        
        // Toast notifications
        showToast(message, type = 'success') {
            if (this.toast.timeoutId) {
                clearTimeout(this.toast.timeoutId);
            }
            this.toast.show = true;
            this.toast.message = message;
            this.toast.type = type;
            
            this.toast.timeoutId = setTimeout(() => {
                this.toast.show = false;
            }, 3000);
        },
        
        // HTTP API Connections
        async fetchStbConfig() {
            try {
                const response = await fetch('/api/stb-config');
                const data = await response.json();
                this.stbConfig = data;
                if (!data.ip_address) {
                    this.resolvedIp = '';
                }
            } catch (error) {
                console.error("Failed to load STB config:", error);
                this.showToast("加载机顶盒凭证配置失败", "error");
            }
        },
        
        // Log Management methods
        async fetchLogs() {
            try {
                const response = await fetch('/api/logs');
                const data = await response.json();
                this.logs = data;
                if (this.logAutoScroll) {
                    this.$nextTick(() => {
                        this.scrollLogsToBottom();
                    });
                }
            } catch (error) {
                console.error("Failed to load logs:", error);
            }
        },

        async clearLogs() {
            try {
                const response = await fetch('/api/logs/clear', { method: 'POST' });
                const res = await response.json();
                if (response.ok && res.status === 'success') {
                    this.logs = [];
                    this.showToast(res.message);
                } else {
                    this.showToast("清空日志失败", "error");
                }
            } catch (error) {
                console.error("Clear logs error:", error);
                this.showToast("通信异常，无法连接日志接口", "error");
            }
        },

        startLogPolling() {
            this.fetchLogs();
            if (!this.logPollTimer) {
                this.logPollTimer = setInterval(() => {
                    this.fetchLogs();
                }, 2000);
            }
        },

        stopLogPolling() {
            if (this.logPollTimer) {
                clearInterval(this.logPollTimer);
                this.logPollTimer = null;
            }
        },

        scrollLogsToBottom() {
            const container = this.$refs.logContainer;
            if (container) {
                container.scrollTop = container.scrollHeight;
            }
        },
        
        async saveStbConfig() {
            this.savingStb = true;
            try {
                const response = await fetch('/api/stb-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.stbConfig)
                });
                const res = await response.json();
                if (response.ok) {
                    if (res.status === 'success') {
                        this.showToast(res.message, "success");
                    } else if (res.status === 'warning') {
                        this.showToast(res.message, "error");
                    } else {
                        this.showToast(res.message || "保存配置失败", "error");
                    }
                    await this.fetchStbConfig();
                    // Fetch logs immediately so the login test details appear in the log viewer
                    await this.fetchLogs();
                } else {
                    this.showToast(res.message || "保存配置失败", "error");
                }
            } catch (error) {
                console.error("Save STB config error:", error);
                this.showToast("通信异常，无法连接后端接口", "error");
            } finally {
                this.savingStb = false;
            }
        },
        
        // Load custom categories configurations
        async fetchVodCategories() {
            try {
                const response = await fetch('/api/vod-categories');
                const data = await response.json();
                
                this.vodCategories = data;
                
                // Default active selection to first category
                if (this.vodCategories && this.vodCategories.length > 0) {
                    this.activeCategoryId = this.vodCategories[0].id;
                }
            } catch (error) {
                console.error("Failed to load VOD categories:", error);
                this.showToast("加载 VOD 自定义分类配置失败", "error");
            }
        },
        
        // Save current VOD categories configuration
        async saveFilters() {
            this.savingFilters = true;
            try {
                const response = await fetch('/api/vod-categories', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.vodCategories)
                });
                const res = await response.json();
                if (response.ok && res.status === 'success') {
                    this.showToast(res.message, "success");
                } else {
                    this.showToast(res.message || "分类配置保存失败", "error");
                }
            } catch (error) {
                console.error("Save categories error:", error);
                this.showToast("通信异常，无法连接分类保存接口", "error");
            } finally {
                this.savingFilters = false;
            }
        },
        
        async fetchSourceTree() {
            this.sourceTreeLoading = true;
            try {
                const response = await fetch('/api/vod-source-tree');
                const data = await response.json();
                // New response format: { tree, refreshing, done, total, last_updated, cached }
                if (Array.isArray(data)) {
                    // Legacy fallback (old API)
                    this.sourceTree = data;
                } else {
                    this.sourceTree = data.tree || [];
                    this.sourceTreeRefreshing = data.refreshing || false;
                    this.sourceTreeMeta = {
                        last_updated: data.last_updated,
                        cached: data.cached,
                        done: data.done || 0,
                        total: data.total || 0
                    };
                    // If a refresh is already running, start polling
                    if (data.refreshing) {
                        this._startPollSourceTree();
                    }
                }
                // By default, keep all nodes collapsed
            } catch (error) {
                console.error("Failed to load VOD source tree:", error);
                this.showToast("加载 VOD 原始分类树数据失败", "error");
            } finally {
                this.sourceTreeLoading = false;
            }
        },
        
        async refreshSourceTree() {
            if (this.sourceTreeRefreshing) {
                this.showToast(`正在抓取中… (${this.sourceTreeMeta.done}/${this.sourceTreeMeta.total})`, 'error');
                return;
            }
            this.sourceTreeRefreshing = true;
            try {
                const res = await fetch('/api/vod-source-tree/refresh', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'already_refreshing') {
                    this.showToast(data.message, 'error');
                } else {
                    this.showToast('已开始抓取，正在从电信服务器拉取分类数据…', 'success');
                    this._startPollSourceTree();
                }
            } catch (e) {
                this.sourceTreeRefreshing = false;
                this.showToast('无法连接后端，刷新请求失败', 'error');
            }
        },
        
        _startPollSourceTree() {
            if (this._sourceTreePollTimer) return;
            this._sourceTreePollTimer = setInterval(async () => {
                try {
                    const res = await fetch('/api/vod-source-tree/status');
                    const status = await res.json();
                    this.sourceTreeMeta.done  = status.done  || 0;
                    this.sourceTreeMeta.total = status.total || 0;
                    
                    if (!status.refreshing) {
                        // Done — reload the tree
                        clearInterval(this._sourceTreePollTimer);
                        this._sourceTreePollTimer = null;
                        this.sourceTreeRefreshing = false;
                        await this.fetchSourceTree();
                        this.showToast(`✅ 分类抓取完成！共更新 ${this.sourceTreeMeta.done} 个分类`, 'success');
                    }
                } catch (e) {
                    // Silently ignore poll errors
                }
            }, 2000); // Poll every 2 seconds
        },
        
        // Tree Node Collapse Toggle
        isExpanded(id) {
            return this.expandedNodes.includes(id);
        },
        
        toggleNode(id) {
            const index = this.expandedNodes.indexOf(id);
            if (index > -1) {
                this.expandedNodes.splice(index, 1);
            } else {
                this.expandedNodes.push(id);
            }
        },
        
        // Top-Level Categories Tab Actions
        selectCategory(id) {
            this.activeCategoryId = id;
        },
        
        // Add a new category tab directly (no modal), enter inline edit immediately
        addCategory() {
            const id = "custom_" + Date.now();
            const newCat = {
                id: id,
                name: '新增分类',
                filters: [
                    {
                        key: "sub_type",
                        name: "分类",
                        value: []
                    }
                ]
            };
            this.vodCategories.push(newCat);
            this.activeCategoryId = id;
            // Enter inline rename immediately so user can type the real name
            this.$nextTick(() => {
                this.editingCatId = id;
                this.$nextTick(() => {
                    const input = document.getElementById('cat-rename-input-' + id);
                    if (input) {
                        input.select();
                        input.focus();
                    }
                });
            });
        },
        
        // Start inline rename for a category tab (no modal)
        renameCategory(cat) {
            this.editingCatId = cat.id;
            this.$nextTick(() => {
                const input = document.getElementById('cat-rename-input-' + cat.id);
                if (input) {
                    input.select();
                    input.focus();
                }
            });
        },
        
        // Commit inline category rename on blur or Enter
        finishRenameCategory(cat) {
            const newName = cat.name.trim();
            if (!newName) {
                cat.name = '新增分类';
            }
            this.editingCatId = null;
        },
        
        deleteCategory(cat, idx) {
            this.modal.show = true;
            this.modal.type = 'delete';
            this.modal.title = '确认删除分类';
            this.modal.targetCat = cat;
            this.modal.targetIdx = idx;
        },
        
        submitModal() {
            const type = this.modal.type;
            if (type === 'delete') {
                const cat = this.modal.targetCat;
                const idx = this.modal.targetIdx;
                this.vodCategories.splice(idx, 1);
                this.showToast(`已删除分类：${cat.name}`);
                
                if (this.activeCategoryId === cat.id) {
                    if (this.vodCategories.length > 0) {
                        this.activeCategoryId = this.vodCategories[0].id;
                    } else {
                        this.activeCategoryId = '';
                    }
                }
            }
            this.closeModal();
        },
        
        closeModal() {
            this.modal.show = false;
            this.modal.type = 'add';
            this.modal.title = '';
            this.modal.value = '';
            this.modal.targetCat = null;
            this.modal.targetIdx = null;
        },
        
        // Native HTML5 Drag and Drop Handlers
        dragStart(event, item) {
            event.dataTransfer.setData('text/plain', JSON.stringify({
                id: item.id,
                name: item.name
            }));
            event.dataTransfer.effectAllowed = 'copy';
        },
        
        handleDrop(event) {
            event.preventDefault();
            try {
                const rawData = event.dataTransfer.getData('text/plain');
                if (!rawData || !rawData.trim().startsWith('{')) return;
                
                const item = JSON.parse(rawData);
                if (!item.id || !item.name) return;
                
                const currentList = this.activeFilterList;
                if (!currentList) return;
                
                // Find where the item was dropped
                const dropY = event.clientY;
                const listItems = Array.from(document.querySelectorAll('.dest-item'));
                let insertIdx = currentList.length;
                
                for (let i = 0; i < listItems.length; i++) {
                    const rect = listItems[i].getBoundingClientRect();
                    const middleY = rect.top + rect.height / 2;
                    if (dropY < middleY) {
                        insertIdx = i;
                        break;
                    }
                }
                
                // Ensure duplicate category ID is not mapped
                const exists = currentList.some(val => val.v === item.id);
                if (exists) {
                    this.showToast(`分类 "${item.name}" 已存在于当前过滤列表中！`, 'error');
                    return;
                }
                
                currentList.splice(insertIdx, 0, { n: item.name, v: item.id });
                this.showToast(`已成功添加映射分类: ${item.name}`, 'success');
            } catch (err) {
                console.error("Drop resolution exception:", err);
            }
        },
        
        // Remove mapped item from configuration
        removeFilterItem(idx) {
            const currentList = this.activeFilterList;
            const removed = currentList.splice(idx, 1);
            if (removed.length > 0) {
                this.showToast(`已删除分类: ${removed[0].n}`);
            }
        },
        
        // Reset current sub-tab config to default settings
        resetFilters() {
            const currentCat = this.vodCategories.find(c => c.id === this.activeCategoryId);
            if (!currentCat) return;
            
            if (confirm(`确认要将 "${currentCat.name}" 的分类配置重置为默认值吗？`)) {
                if (currentCat.id === 'movies') {
                    currentCat.filters = JSON.parse(JSON.stringify(DEFAULT_MOVIES_FILTERS));
                } else if (currentCat.id === 'series') {
                    currentCat.filters = JSON.parse(JSON.stringify(DEFAULT_SERIES_FILTERS));
                } else {
                    currentCat.filters = [
                        {
                            key: "sub_type",
                            name: "分类",
                            value: []
                        }
                    ];
                }
                this.showToast("已成功重置为默认分类", "success");
            }
        },
        
        // Double-click to rename filter mappings
        editName(idx) {
            this.editingIdx = idx;
            this.$nextTick(() => {
                const inputs = this.$refs.nameInput;
                if (inputs) {
                    if (Array.isArray(inputs)) {
                        if (inputs[0]) inputs[0].focus();
                    } else {
                        inputs.focus();
                    }
                }
            });
        },
        
        // Initialize SortableJS for right list sorting
        initSortable() {
            const el = document.getElementById('dest-sortable-list');
            if (!el) return;
            
            // Clean up existing instances to prevent leaks
            if (this.sortableInstance) {
                this.sortableInstance.destroy();
                this.sortableInstance = null;
            }
            
            this.sortableInstance = Sortable.create(el, {
                animation: 150,
                handle: '.drag-handle',
                ghostClass: 'sortable-ghost',
                onEnd: (evt) => {
                    const oldIndex = evt.oldIndex;
                    const newIndex = evt.newIndex;
                    
                    if (oldIndex === undefined || newIndex === undefined || oldIndex === newIndex) {
                        return;
                    }
                    
                    const currentCat = this.vodCategories.find(c => c.id === this.activeCategoryId);
                    if (!currentCat || !currentCat.filters || !currentCat.filters[0]) return;
                    
                    const listCopy = [...currentCat.filters[0].value];
                    
                    // Move the item in copy list
                    const [movedItem] = listCopy.splice(oldIndex, 1);
                    listCopy.splice(newIndex, 0, movedItem);
                    
                    // Crucial: Undo Sortable's DOM manipulation immediately so Vue can reconcile it properly.
                    const parent = evt.to;
                    const children = Array.from(parent.children);
                    if (newIndex < oldIndex) {
                        parent.insertBefore(evt.item, children[oldIndex + 1]);
                    } else {
                        parent.insertBefore(evt.item, children[oldIndex]);
                    }
                    
                    // Save copy back to model triggers Vue reactivity render cleanly
                    currentCat.filters[0].value = listCopy;
                }
            });
        },
        
        initTabsSortable() {
            const el = document.querySelector('.vod-tabs-scroll');
            if (!el) return;
            
            if (this.tabsSortableInstance) {
                this.tabsSortableInstance.destroy();
                this.tabsSortableInstance = null;
            }
            
            this.tabsSortableInstance = Sortable.create(el, {
                animation: 150,
                ghostClass: 'sortable-ghost',
                filter: 'input',
                preventOnFilter: false,
                onEnd: (evt) => {
                    const oldIndex = evt.oldIndex;
                    const newIndex = evt.newIndex;
                    
                    if (oldIndex === undefined || newIndex === undefined || oldIndex === newIndex) {
                        return;
                    }
                    
                    const listCopy = [...this.vodCategories];
                    const [movedItem] = listCopy.splice(oldIndex, 1);
                    listCopy.splice(newIndex, 0, movedItem);
                    
                    // Undo DOM change for Vue
                    const parent = evt.to;
                    const children = Array.from(parent.children);
                    if (newIndex < oldIndex) {
                        parent.insertBefore(evt.item, children[oldIndex + 1]);
                    } else {
                        parent.insertBefore(evt.item, children[oldIndex]);
                    }
                    
                    this.vodCategories = listCopy;
                    this.showToast("分类顺序已调整，保存请点击右侧「保存过滤器配置」！", "success");
                }
            });
        }
    }
});

const appInstance = app.mount('#app');
