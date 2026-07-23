/**
 * ToastNotification — 全局提示通知组件
 * 
 * 从根组件 provide('showToast') 获取弹窗控制方法
 */
const ToastNotification = {
    name: 'ToastNotification',

    inject: ['showToast'],

    data() {
        return {
            visible: false,
            message: '',
            type: 'success',
            timer: null
        };
    },

    methods: {
        show(msg, type = 'success') {
            if (this.timer) clearTimeout(this.timer);
            this.message = msg;
            this.type = type;
            this.visible = true;
            this.timer = setTimeout(() => { this.visible = false; }, 3000);
        }
    },

    template: `
        <transition name="slide-down">
            <div v-if="visible" :class="['toast-notification', type]">
                <span class="toast-icon">{{ type === 'success' ? '✅' : '❌' }}</span>
                <span class="toast-message">{{ message }}</span>
            </div>
        </transition>
    `
};
