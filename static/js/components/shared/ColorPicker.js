/**
 * ColorPicker — 颜色选择器组件
 * 
 * Props:
 *   color - 当前颜色值 (String)
 * 
 * Events:
 *   select - 选中颜色时触发，携带选中颜色值
 * 
 * 用法：<color-picker :color="item.color" @select="item.color = $event"></color-picker>
 */
const ColorPicker = {
    name: 'ColorPicker',

    props: {
        color: { type: String, default: '#6366f1' }
    },

    emits: ['select'],

    data() {
        return {
            open: false,
            presetColors: [
                '#ef4444', '#f97316', '#eab308', '#22c55e', '#14b8a6',
                '#3b82f6', '#6366f1', '#8b5cf6', '#ec4899', '#64748b',
                '#78716c', '#a3e635', '#06b6d4', '#a855f7', '#f43f5e'
            ]
        };
    },

    template: `
        <div class="color-picker-row">
            <span class="color-dot"
                  :style="{ background: color || '#6366f1' }"
                  @click.stop="open = !open"></span>
            <div v-if="open" class="color-picker-mask" @click="open = false"></div>
            <div v-if="open" class="color-picker-popup" @click.stop>
                <span v-for="c in presetColors" :key="c" class="color-dot"
                      :style="{ background: c, boxShadow: color === c ? '0 0 0 2px #fff, 0 0 0 4px ' + c : '' }"
                      @click="$emit('select', c); open = false"></span>
            </div>
        </div>
    `
};
