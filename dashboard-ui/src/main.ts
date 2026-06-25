/* main.ts — 工坊台入口
 * 样式顺序：tailwind(base/components/utilities) → tokens(:root 变量) → base(字体/重置/无障碍)
 * base 最后加载以覆盖 tailwind preflight 的默认字体/边距。 */
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import './styles/tailwind.css';
import './styles/tokens.css';
import './styles/base.css';

createApp(App).use(createPinia()).use(router).mount('#app');
