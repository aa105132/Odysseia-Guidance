<script setup lang="ts">
/* AppTopBar — 顶栏：当前路由标题（meta.title 或 route.name）+ 刷新 + 登出。 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { RefreshCw, LogOut } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

const emit = defineEmits<{ (e: 'refresh'): void }>();

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const toast = useToastStore();

const title = computed(
  () => (route.meta.title as string | undefined) ?? (route.name?.toString() ?? '工坊台'),
);

function onLogout(): void {
  auth.logout();
  toast.push({ type: 'info', message: '已退出工坊' });
  router.push('/login');
}
</script>

<template>
  <header class="topbar" role="banner">
    <BaseSectionTitle :title="title" />
    <div class="topbar__actions">
      <BaseButton variant="ghost" size="sm" :icon="RefreshCw" @click="emit('refresh')">刷新</BaseButton>
      <BaseButton variant="ghost" size="sm" :icon="LogOut" @click="onLogout">登出</BaseButton>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-8);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
}
.topbar__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--space-2);
}
</style>
