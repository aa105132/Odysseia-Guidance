/* router/index.ts — 路由表 + 鉴权守卫
 * 20 业务视图 + 1 登录页，全部懒加载。未登录访问受保护路由跳 /login（带 redirect 回跳）；
 * 已登录访问 /login 跳 /overview。 */
import { createRouter, createWebHistory } from 'vue-router';
import type { RouteRecordRaw } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { title: '总览' },
  },
  {
    path: '/stats',
    name: 'stats',
    component: () => import('@/views/StatsView.vue'),
    meta: { title: '统计页' },
  },
  {
    path: '/ai',
    name: 'ai',
    component: () => import('@/views/AIView.vue'),
    meta: { title: 'AI 设置' },
  },
  {
    path: '/imagen',
    name: 'imagen',
    component: () => import('@/views/ImagenView.vue'),
    meta: { title: '绘图设置' },
  },
  {
    path: '/comfyui',
    name: 'comfyui',
    component: () => import('@/views/ComfyUIView.vue'),
    meta: { title: 'ComfyUI 设置' },
  },
  {
    path: '/video',
    name: 'video',
    component: () => import('@/views/VideoView.vue'),
    meta: { title: '视频设置' },
  },
  {
    path: '/voice',
    name: 'voice',
    component: () => import('@/views/VoiceView.vue'),
    meta: { title: '语音设置' },
  },
  {
    path: '/novelai',
    name: 'novelai',
    component: () => import('@/views/NovelAIView.vue'),
    meta: { title: 'NovelAI 设置' },
  },
  {
    path: '/web-search',
    name: 'web-search',
    component: () => import('@/views/WebSearchView.vue'),
    meta: { title: '网络搜索' },
  },
  {
    path: '/image-search',
    name: 'image-search',
    component: () => import('@/views/ImageSearchView.vue'),
    meta: { title: '图片搜索' },
  },
  {
    path: '/embedding',
    name: 'embedding',
    component: () => import('@/views/EmbeddingView.vue'),
    meta: { title: '向量嵌入' },
  },
  {
    path: '/coin',
    name: 'coin',
    component: () => import('@/views/CoinView.vue'),
    meta: { title: '货币设置' },
  },
  {
    path: '/moderation',
    name: 'moderation',
    component: () => import('@/views/ModerationView.vue'),
    meta: { title: '管理设置' },
  },
  {
    path: '/emoji',
    name: 'emoji',
    component: () => import('@/views/EmojiView.vue'),
    meta: { title: '表情管理' },
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/KnowledgeView.vue'),
    meta: { title: '知识库' },
  },
  {
    path: '/thread-auto-speaker',
    name: 'thread-auto-speaker',
    component: () => import('@/views/ThreadAutoSpeakerView.vue'),
    meta: { title: '自动暖贴' },
  },
  {
    path: '/spring-festival',
    name: 'spring-festival',
    component: () => import('@/views/SpringFestivalView.vue'),
    meta: { title: '新春活动' },
  },
  {
    path: '/summary',
    name: 'summary',
    component: () => import('@/views/SummaryView.vue'),
    meta: { title: '年度总结' },
  },
  {
    path: '/daily-outfit',
    name: 'daily-outfit',
    component: () => import('@/views/DailyOutfitView.vue'),
    meta: { title: '每日换装' },
  },
  {
    path: '/system',
    name: 'system',
    component: () => import('@/views/SystemView.vue'),
    meta: { title: '系统' },
  },
  { path: '/', redirect: '/overview' },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
];

const router = createRouter({
  // base 对齐 Vite base 与 FastAPI mount：生产 /static/，dev 期 Vite 注入 import.meta.env.BASE_URL
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior() {
    return { top: 0 };
  },
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.meta.public !== true && !auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } };
  }
  if (to.name === 'login' && auth.isAuthenticated) {
    return { path: '/overview' };
  }
  return;
});

export default router;
