<script setup lang="ts">
/* App.vue — 工坊台壳：左侧导航 + 顶栏 + 路由出口 + 全局 toast。
 * 登录态门控由 router.beforeEach 处理；此处按路由 meta.public 决定是否渲染壳。
 * 登录页（public）全屏渲染；其余路由渲染侧栏 + 顶栏 + 内容区。
 * 刷新机制：provide registerRefresh，当前视图 onMounted 注册自己的 force 刷新函数；
 * 顶栏 emit refresh → 调用已注册函数，避免 remount 整个视图。 */
import { ref, computed, provide } from 'vue';
import { useRoute } from 'vue-router';
import AppSidebar from '@/components/layout/AppSidebar.vue';
import AppTopBar from '@/components/layout/AppTopBar.vue';
import BaseToastContainer from '@/components/ui/BaseToastContainer.vue';

const route = useRoute();
const isPublic = computed(() => route.meta.public === true);

// 当前视图注册的 force 刷新函数；未注册时顶栏刷新无操作
const refreshFn = ref<(() => Promise<void>) | null>(null);

// provide 契约：视图 onMounted 调 registerRefresh(fn) 注册自己的 force 刷新
provide('registerRefresh', (fn: () => Promise<void>): void => {
  refreshFn.value = fn;
});

function onRefresh(): void {
  refreshFn.value?.();
}
</script>

<template>
  <BaseToastContainer />
  <template v-if="isPublic">
    <router-view />
  </template>
  <template v-else>
    <div class="shell">
      <AppSidebar class="shell__nav" />
      <div class="shell__main">
        <AppTopBar class="shell__topbar" @refresh="onRefresh" />
        <main class="shell__content" id="main-content">
          <router-view v-slot="{ Component }">
            <component :is="Component" />
          </router-view>
        </main>
      </div>
    </div>
  </template>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 16.5rem 1fr;
  min-height: 100vh;
  background: var(--bg-base);
}
.shell__main {
  display: flex;
  flex-direction: column;
  min-width: 0; /* 防 grid 溢出 */
}
.shell__content {
  flex: 1;
  padding: var(--space-8);
  overflow-y: auto;
}
/* 阶段0暂不处理移动端抽屉，后续补 */
@media (max-width: 768px) {
  .shell {
    grid-template-columns: 1fr;
  }
  .shell__nav {
    display: none;
  }
}
</style>
