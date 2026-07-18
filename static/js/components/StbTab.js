/**
 * StbTab — 系统配置 Tab 页
 * 
 * 包含：定时自动同步管理 / 机顶盒鉴权参数 / 系统运行日志
 * 
 * Inject: showToast, simStatus, syncStatus, epgSyncStatus, syncingLive,
 *          triggerSync, triggerEpgSync, triggerLiveSync, formatTime
 */
const StbTab = {
    name: 'StbTab',

    inject: [
        'showToast', 'simStatus',
        'syncingLive',
        'triggerSync', 'triggerEpgSync', 'triggerLiveSync',
        'formatTime'
    ],

    data() {
        return {
            /* STB 配置 */
            stbConfig: { user_id: '', stb_id: '', mac_address: '', base_url: '', des_key: '', ip_address: '' },
            resolvedIp: '',
            savingStb: false,

            /* 定时同步 */
            schedulerConfig: { live_sync_hour: 0, vod_sync_hour: 1, epg_sync_hour: 1, scheduler_enabled_bool: true, live_sync_enabled_bool: true, vod_sync_enabled_bool: true, epg_sync_enabled_bool: true },
            schedulerStatus: { running: false, config: {}, tasks: {} },
            savingScheduler: false,
            taskLabels: { live: '直播频道', vod: 'VOD 点播', epg: 'EPG 节目单' },
            schedulerTasks: [
                { key: 'live', icon: '📺', name: '直播', hourKey: 'live_sync_hour', enabledKey: 'live_sync_enabled_bool' },
                { key: 'epg',  icon: '📅', name: 'EPG', hourKey: 'epg_sync_hour',  enabledKey: 'epg_sync_enabled_bool' },
                { key: 'vod',  icon: '🎬', name: 'VOD', hourKey: 'vod_sync_hour',  enabledKey: 'vod_sync_enabled_bool' }
            ],

            /* 日志 */
            logs: [],
            logLevelFilter: 'ALL',
            logAutoScroll: true,
            logPollTimer: null,
            schedulerStatusTimer: null
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
        }
    },

    watch: {
        'schedulerConfig.scheduler_enabled_bool'(newVal) {
            if (!newVal) {
                this.schedulerConfig.live_sync_enabled_bool = false;
                this.schedulerConfig.vod_sync_enabled_bool = false;
                this.schedulerConfig.epg_sync_enabled_bool = false;
            }
        },
        logLevelFilter() {
            this.fetchLogs();
        }
    },

    created() {
        this.fetchStbConfig();
        this.fetchSchedulerConfig();
        this.fetchSchedulerStatus();
        this.startLogPolling();
        if (!this.schedulerStatusTimer) {
            this.schedulerStatusTimer = setInterval(() => this.fetchSchedulerStatus(), 10000);
        }
    },

    beforeUnmount() {
        [this.logPollTimer, this.schedulerStatusTimer].forEach(t => {
            if (t) { clearInterval(t); }
        });
    },

    methods: {
        /* ---------- STB Config ---------- */
        async fetchStbConfig() {
            try {
                this.stbConfig = await stbService.getStbConfig();
            } catch (e) { /* silent */ }
        },

        async saveStbConfig() {
            this.savingStb = true;
            try {
                const res = await stbService.saveStbConfig(this.stbConfig);
                this.showToast(res.message, res.status === 'warning' ? 'error' : 'success');
                await this.fetchStbConfig();
            } catch (e) {
                this.showToast(e.message || '通信异常', 'error');
            } finally { this.savingStb = false; }
        },

        /* ---------- 定时同步 ---------- */
        async fetchSchedulerConfig() {
            try {
                const c = await stbService.getSchedulerConfig();
                this.schedulerConfig = {
                    live_sync_hour: parseInt(c.live_sync_hour ?? 0) || 0,
                    vod_sync_hour: parseInt(c.vod_sync_hour ?? 1) || 0,
                    epg_sync_hour: parseInt(c.epg_sync_hour ?? 1) || 0,
                    scheduler_enabled_bool: c.scheduler_enabled !== '0',
                    live_sync_enabled_bool: c.live_sync_enabled !== '0',
                    vod_sync_enabled_bool: c.vod_sync_enabled !== '0',
                    epg_sync_enabled_bool: c.epg_sync_enabled !== '0'
                };
            } catch (e) { /* silent */ }
        },

        async fetchSchedulerStatus() {
            try {
                this.schedulerStatus = await stbService.getSchedulerStatus();
            } catch (e) { /* silent */ }
        },

        async saveSchedulerConfig() {
            this.savingScheduler = true;
            try {
                await stbService.saveSchedulerConfig({
                    live_sync_hour: String(this.schedulerConfig.live_sync_hour),
                    vod_sync_hour: String(this.schedulerConfig.vod_sync_hour),
                    epg_sync_hour: String(this.schedulerConfig.epg_sync_hour),
                    scheduler_enabled: this.schedulerConfig.scheduler_enabled_bool ? '1' : '0',
                    live_sync_enabled: this.schedulerConfig.live_sync_enabled_bool ? '1' : '0',
                    vod_sync_enabled: this.schedulerConfig.vod_sync_enabled_bool ? '1' : '0',
                    epg_sync_enabled: this.schedulerConfig.epg_sync_enabled_bool ? '1' : '0'
                });
                this.showToast('定时设置已保存，即时生效');
                this.fetchSchedulerStatus();
            } catch (e) {
                this.showToast(e.message || '网络错误', 'error');
            } finally { this.savingScheduler = false; }
        },

        manualSchedulerSync(key) {
            if (key === 'live') return this.triggerLiveSync();
            if (key === 'epg')  return this.triggerEpgSync();
            if (key === 'vod')  return this.triggerSync();
        },

        taskStatusText(key) {
            const t = this.schedulerStatus.tasks?.[key];
            const enabled = this.schedulerConfig[`${key}_sync_enabled_bool`];
            if (!enabled) return '⛔ 已禁用';
            if (!t) return '⏳ 待执行';
            if (t.done_today) return '✅ 今日已同步';
            if (t.gave_up) return '❌ 今日放弃';
            if (t.retrying) return '🔁 待重试';
            return '⏳ 待执行';
        },

        taskStatusClass(key) {
            const t = this.schedulerStatus.tasks?.[key];
            const enabled = this.schedulerConfig[`${key}_sync_enabled_bool`];
            if (!enabled) return 'text-muted';
            if (!t) return 'text-muted';
            if (t.done_today) return 'text-success';
            if (t.gave_up) return 'text-error';
            return 'text-muted';
        },

        /* ---------- 日志 ---------- */
        async fetchLogs() {
            try {
                const raw = await stbService.getLogs(200, this.logLevelFilter);
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
                await stbService.clearLogs();
                this.logs = [];
                this.showToast('日志已清空');
            } catch (e) {
                this.showToast('清空失败', 'error');
            }
        },

        startLogPolling() {
            this.fetchLogs();
            if (!this.logPollTimer) {
                this.logPollTimer = setInterval(() => this.fetchLogs(), 2000);
            }
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
                try {
                    ta.select();
                    document.execCommand('copy');
                    this.showToast('已复制到剪贴板');
                } finally {
                    document.body.removeChild(ta);
                }
            } catch (e) {
                this.showToast('复制失败', 'error');
            }
        }
    },

    template: `<!-- ↓↓ 由 app.js 中提取的 StbTab template ↓↓ -->
        <div class="card-grid cols-3">
            <div class="card form-card">
                <div class="card-header">
                    <h3>定时自动同步管理</h3>
                    <div class="scheduler-master">
                        <span class="status-pill" :class="schedulerStatus.running ? 'on' : 'off'">
                            {{ schedulerStatus.running ? '运行中' : '已停止' }}
                        </span>
                        <label class="switch-toggle">
                            <input type="checkbox" id="scheduler-master" name="scheduler_enabled" v-model="schedulerConfig.scheduler_enabled_bool" @change="saveSchedulerConfig">
                            <span class="switch-slider"></span>
                        </label>
                    </div>
                </div>
                <div class="scheduler-tasks">
                    <div class="task-row" v-for="task in schedulerTasks" :key="task.key">
                        <div class="task-head">
                            <div class="task-title">
                                <span class="task-icon">{{ task.icon }}</span>
                                <span class="task-name">{{ task.name }} 同步时刻</span>
                            </div>
                            <label class="switch-toggle">
                                <input type="checkbox" :name="task.key + '_enabled'"
                                       v-model="schedulerConfig[task.enabledKey]"
                                       :disabled="!schedulerConfig.scheduler_enabled_bool"
                                       @change="saveSchedulerConfig">
                                <span class="switch-slider"></span>
                            </label>
                        </div>
                        <div class="task-body">
                            <select class="hour-select" :id="task.key + '-hour'" :name="task.key + '_hour'"
                                    v-model.number="schedulerConfig[task.hourKey]"
                                    :disabled="!schedulerConfig.scheduler_enabled_bool || !schedulerConfig[task.enabledKey]"
                                    @change="saveSchedulerConfig">
                                <option v-for="h in 24" :key="task.key+h" :value="h-1">每天 {{ String(h-1).padStart(2,'0') }}:00</option>
                            </select>
                            <div class="task-status">
                                <span :class="taskStatusClass(task.key)">{{ taskStatusText(task.key) }}</span>
                                <span class="text-muted" v-if="schedulerStatus.tasks[task.key]?.last_sync_time">
                                    上次 {{ formatTime(schedulerStatus.tasks[task.key].last_sync_time) }}
                                </span>
                                <button class="btn btn-secondary btn-sm"
                                        @click="manualSchedulerSync(task.key)"
                                        :disabled="task.key === 'live' ? (syncingLive || !simStatus.is_authenticated) : false">
                                    🔄 手动同步
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card form-card">
                <div class="card-header">
                    <h3>机顶盒鉴权参数</h3>
                    <span class="badge">stb_config.json</span>
                </div>
                <div class="form-grid">
                    <div class="form-group">
                        <label for="stb-user-id">业务账号 (User ID)</label>
                        <input type="text" id="stb-user-id" v-model="stbConfig.user_id" placeholder="例如: 1234567890123456">
                    </div>
                    <div class="form-group">
                        <label for="stb-device-id">终端设备 ID (STBID)</label>
                        <input type="text" id="stb-device-id" v-model="stbConfig.stb_id" placeholder="例如: 001003990060262001123456789ABCDE">
                    </div>
                    <div class="form-group">
                        <label for="stb-mac">物理 MAC 地址</label>
                        <input type="text" id="stb-mac" v-model="stbConfig.mac_address" placeholder="例如: A0:B1:C2:D3:E4:F5">
                    </div>
                    <div class="form-group">
                        <label for="stb-base-url">EPG 网关基础 URL</label>
                        <input type="text" id="stb-base-url" v-model="stbConfig.base_url" placeholder="例如: http://10.123.123.123:33200">
                    </div>
                    <div class="form-group">
                        <label for="stb-des-key">动态算密 Key (DES Key)</label>
                        <input type="text" id="stb-des-key" v-model="stbConfig.des_key" placeholder="默认为 00000000">
                    </div>
                    <div class="form-group">
                        <label for="stb-ip">网卡出网 IP 地址 (留空开启动态探测)</label>
                        <input type="text" id="stb-ip" v-model="stbConfig.ip_address" placeholder="空值将利用路由表自动探测绑定">
                    </div>
                </div>
                <div class="form-actions">
                    <button class="btn btn-primary w-full" @click="saveStbConfig" :disabled="savingStb">
                        <span class="spinner" v-if="savingStb"></span>
                        {{ savingStb ? '正在保存并验证...' : '💾 保存配置并测试登录' }}
                    </button>
                </div>
            </div>

            <div class="card status-card">
                <div class="card-header">
                    <h3>仿真鉴权运行状态</h3>
                </div>
                <div class="status-list horizontal-stats">
                    <div class="status-item">
                        <span class="status-label">当前运行 IP</span>
                        <span class="status-val highlight">{{ stbConfig.ip_address || resolvedIp || '未获取 (空值等待动态分配)' }}</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">IP 分配模式</span>
                        <span class="status-val">{{ stbConfig.ip_address ? '静态手动指定' : '系统动态探测路由' }}</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">DES 模块检测</span>
                        <span class="status-val text-success">✔️ Crypto.Cipher 动态算密激活</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">鉴权状态</span>
                        <span :class="simStatus.is_authenticated ? 'status-val text-success' : 'status-val text-muted'">
                            {{ simStatus.is_authenticated ? '✅ 已登录' : '⭕ 未登录' }}
                        </span>
                    </div>
                    <div class="status-item" v-if="simStatus.is_authenticated">
                        <span class="status-label">EPG 网关</span>
                        <span class="status-val highlight">{{ simStatus.epg_base_url || '—' }}</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">JSESSIONID</span>
                        <span class="status-val token-val" v-if="simStatus.jsessionid" :title="simStatus.jsessionid">
                            <span class="token-text">{{ maskToken(simStatus.jsessionid) }}</span>
                            <button class="copy-btn" @click="copyToClipboard(simStatus.jsessionid)" title="复制">📋</button>
                        </span>
                        <span class="status-val text-muted" v-else>—</span>
                    </div>
                    <div class="status-item">
                        <span class="status-label">UserToken</span>
                        <span class="status-val token-val" v-if="simStatus.user_token" :title="simStatus.user_token">
                            <span class="token-text">{{ maskToken(simStatus.user_token) }}</span>
                            <button class="copy-btn" @click="copyToClipboard(simStatus.user_token)" title="复制">📋</button>
                        </span>
                        <span class="status-val text-muted" v-else>—</span>
                    </div>
                    <div class="status-item full">
                        <span class="status-label">说明</span>
                        <p class="status-desc">机顶盒配置用于与电信 EPG 网关建立安全链路以获取点播播放 URL。建议直接保持 IP 地址为空，以允许程序根据宿主机当前连通 EPG 节点的路由状况自动生成正确的源 IP。</p>
                    </div>
                </div>
            </div>
        </div>

        <div class="card log-card">
            <div class="card-header">
                <div class="header-left">
                    <h3>系统运行日志</h3>
                </div>
                <div class="log-actions">
                    <select id="log-level" name="log_level" v-model="logLevelFilter">
                        <option value="ALL">全部日志 (ALL)</option>
                        <option value="DEBUG">调试 (DEBUG及以上)</option>
                        <option value="INFO">信息 (INFO及以上)</option>
                        <option value="WARNING">警告 (WARN及以上)</option>
                        <option value="ERROR">错误 (ERROR及以上)</option>
                    </select>
                    <button class="btn btn-secondary btn-sm" @click="clearLogs">🗑️ 清空日志</button>
                    <label class="auto-scroll-label">
                        <input type="checkbox" id="log-auto-scroll" name="auto_scroll" v-model="logAutoScroll"> <label for="log-auto-scroll">自动滚动</label>
                    </label>
                </div>
            </div>
            <div class="log-container" ref="logContainer">
                <div v-if="filteredLogs.length === 0" class="log-empty">暂无相关运行日志</div>
                <div v-else v-for="(log, idx) in filteredLogs" :key="idx" :class="['log-item', log.level]">
                    <span class="log-time">{{ log.time }}</span>
                    <span :class="['log-level-text', log.level.toLowerCase()]">{{ log.level }}</span>
                    <span class="log-message">{{ log.message }}</span>
                </div>
            </div>
        </div>
    `
};
