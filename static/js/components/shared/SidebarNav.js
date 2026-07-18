/**
 * SidebarNav — 侧边栏导航 + 主题切换
 * 
 * Props:  activeTab - 当前活跃 tab
 *         theme     - 主题 dark/light
 * Events: nav(tab)  - tab 切换
 *         toggle-theme - 主题切换
 */
const SidebarNav = {
    name: 'SidebarNav',

    props: {
        activeTab: { type: String, required: true },
        theme: { type: String, required: true }
    },

    emits: ['nav', 'toggle-theme'],

    template: `
        <aside class="sidebar">
            <div class="brand">
                <span class="logo-icon">📺</span>
                <h2>IPTV-Toolkit</h2>
            </div>
            <nav class="nav-menu">
                <a href="#" :class="['nav-item', { active: activeTab === 'stb' }]" @click.prevent="$emit('nav', 'stb')">
                    <span class="nav-icon">⚙️</span> 系统配置
                </a>
                <a href="#" :class="['nav-item', { active: activeTab === 'vod' }]" @click.prevent="$emit('nav', 'vod')">
                    <span class="nav-icon">🎬</span> VOD 点播管理
                </a>
                <a href="#" :class="['nav-item', { active: activeTab === 'live' }]" @click.prevent="$emit('nav', 'live')">
                    <span class="nav-icon">📺</span> 直播频道管理
                </a>
                <a href="#" :class="['nav-item', { active: activeTab === 'epg' }]" @click.prevent="$emit('nav', 'epg')">
                    <span class="nav-icon">📅</span> EPG 节目管理
                </a>
            </nav>
            <div class="sidebar-footer">
                <button class="theme-toggle-btn" @click="$emit('toggle-theme')" title="切换深色/浅色主题">
                    <span v-if="theme === 'dark'">☀️ 浅色模式</span>
                    <span v-else>🌙 深色模式</span>
                </button>
            </div>
        </aside>
    `
};
