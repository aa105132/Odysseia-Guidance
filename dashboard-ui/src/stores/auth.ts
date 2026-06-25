/* auth.ts — 鉴权状态（Pinia）
 * token 持久化到 localStorage；router 守卫与 client 共享 isAuthenticated。
 * 现状对接后端 HTTPBearer + DASHBOARD_SECRET，不升级机制。 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';

const STORAGE_KEY = 'odysseia_dashboard_token';

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(STORAGE_KEY) ?? '');
  const isAuthenticated = computed(() => !!token.value);

  function login(key: string): void {
    token.value = key;
    try {
      localStorage.setItem(STORAGE_KEY, key);
    } catch {
      /* 无痕模式忽略持久化失败 */
    }
  }

  function logout(): void {
    token.value = '';
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* 无痕模式忽略 */
    }
  }

  return { token, isAuthenticated, login, logout };
});
