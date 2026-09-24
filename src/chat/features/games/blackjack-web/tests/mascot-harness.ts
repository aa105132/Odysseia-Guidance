import { createApp, h, ref } from 'vue';
import YueyueMascot from '../src/YueyueMascot.vue';
import { installActivityViewport } from '../src/activityViewport';
import { setVoiceEnabled } from '../src/gameAudio';
import '../src/style.css';

const visible = ref(true);
const removeViewport = installActivityViewport();
const app = createApp({
  setup: () => () => h('main', { style: 'width:100%;height:100%;padding:24px;display:flex;flex-direction:column;background:#d9ded7' }, [
    h('button', { onClick: () => setVoiceEnabled(false), style: 'align-self:flex-start' }, '关闭语音'),
    h('button', { onClick: () => { visible.value = false; }, style: 'align-self:flex-start' }, '卸载月月'),
    visible.value ? h(YueyueMascot, { message: '选好玩法，我们就开局吧。' }) : null,
  ]),
});
app.mount('#app');
window.addEventListener('pagehide', () => { app.unmount(); removeViewport(); }, { once: true });
