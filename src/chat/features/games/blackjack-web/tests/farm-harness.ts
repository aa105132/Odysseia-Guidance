import { createApp } from 'vue';
import FarmGame from '../src/FarmGame.vue';
import '../src/style.css';

// 独立组件的浏览器测试入口，使用实际 Vue 生命周期与 HTTP 行为。
document.body.style.overflow = 'hidden';
const portal = document.createElement('div');
portal.id = 'app';
portal.style.pointerEvents = 'none';
document.body.appendChild(portal);
document.getElementById('farm-test-app')!.style.cssText = 'height:100dvh;display:flex;padding:12px;background:#dce5d2';
async function apiCall<T>(endpoint: string, method: 'GET' | 'POST', body?: unknown): Promise<T> {
  const response = await fetch(endpoint, { method, headers: { 'Content-Type': 'application/json' }, body: method === 'POST' ? JSON.stringify(body) : undefined });
  if (!response.ok) throw Object.assign(new Error((await response.json()).detail), { status: response.status });
  return response.json();
}
createApp(FarmGame, {
  profile: { user_id: '123456789012345678', username: '灵圃主人', avatar_url: '/ui/player-avatar.svg', balance: 5000 },
  apiCall,
}).mount('#farm-test-app');
