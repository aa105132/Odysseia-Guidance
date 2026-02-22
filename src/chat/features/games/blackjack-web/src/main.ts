import { createApp } from 'vue';
import './style.css';

async function bootstrap() {
    try {
        const { default: App } = await import('./App.vue');
        createApp(App).mount('#app');
    } catch (error) {
        console.error('[Bootstrap] Failed to mount app:', error);
        const appRoot = document.getElementById('app');
        if (appRoot) {
            appRoot.innerHTML = `
                <div style="padding:16px;font-family:Arial,sans-serif;color:#fff;background:#1f2937;min-height:100vh;">
                    <h2 style="margin:0 0 12px 0;">应用加载失败</h2>
                    <p style="margin:0;line-height:1.6;">
                        前端初始化异常，请打开控制台查看具体错误并联系管理员。
                    </p>
                </div>
            `;
        }
    }
}

void bootstrap();
