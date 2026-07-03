<script setup lang="ts">
/* App.vue — 工坊台壳：左侧导航 + 顶栏 + 路由出口 + 全局 toast。
 * 登录态门控由 router.beforeEach 处理；此处按路由 meta.public 决定是否渲染壳。
 * 登录页（public）全屏渲染；其余路由渲染侧栏 + 顶栏 + 内容区。
 * 移动端侧栏作为抽屉打开，切换路由或点击遮罩关闭。
 * 刷新机制：provide registerRefresh，当前视图 onMounted 注册自己的 force 刷新函数；
 * 顶栏 emit refresh → 调用已注册函数，避免 remount 整个视图。 */
import { ref, computed, provide, watch } from 'vue';
import { useRoute } from 'vue-router';
import AppSidebar from '@/components/layout/AppSidebar.vue';
import AppTopBar from '@/components/layout/AppTopBar.vue';
import BaseToastContainer from '@/components/ui/BaseToastContainer.vue';

const route = useRoute();
const isPublic = computed(() => route.meta.public === true);
const navOpen = ref(false);

// 当前视图注册的 force 刷新函数；未注册时顶栏刷新无操作
const refreshFn = ref<(() => Promise<void>) | null>(null);

// provide 契约：视图 onMounted 调 registerRefresh(fn) 注册自己的 force 刷新
provide('registerRefresh', (fn: () => Promise<void>): void => {
  refreshFn.value = fn;
});

function onRefresh(): void {
  refreshFn.value?.();
}

function openNav(): void {
  navOpen.value = true;
}

function closeNav(): void {
  navOpen.value = false;
}

watch(
  () => route.fullPath,
  () => {
    navOpen.value = false;
  },
);
</script>

<template>
  <BaseToastContainer />
  <template v-if="isPublic">
    <router-view />
  </template>
  <template v-else>
    <div class="shell" :class="{ 'is-nav-open': navOpen }">
      <AppSidebar class="shell__nav" @navigate="closeNav" />
      <button
        v-if="navOpen"
        class="shell__scrim"
        type="button"
        aria-label="关闭导航"
        @click="closeNav"
      />
      <div class="shell__main">
        <AppTopBar class="shell__topbar" @refresh="onRefresh" @menu="openNav" />
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

.shell__scrim {
  display: none;
}

@media (max-width: 768px) {
  .shell {
    grid-template-columns: 1fr;
  }
  .shell__nav {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 40;
    width: min(18rem, calc(100vw - 3rem));
    transform: translateX(-100%);
    transition: transform var(--dur-view) var(--ease-out-quart);
    box-shadow: var(--shadow-lg);
  }
  .shell.is-nav-open .shell__nav {
    transform: translateX(0);
  }
  .shell__scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 30;
    border: 0;
    background: oklch(0.1 0.01 75 / 0.62);
    cursor: pointer;
  }
  .shell__content {
    padding: var(--space-4);
  }
}

@media (prefers-reduced-motion: reduce) {
  .shell__nav {
    transition: none;
  }
}
</style>
