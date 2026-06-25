<script setup lang="ts">
/* AppSidebar — 主导航，4分组20项（结构对齐旧 SPA L567-691）。
 * 激活态：琥珀左边框 2px + accent-subtle 暖底 + semibold（router-link-exact-active）。
 * 顶部锚点：灵石图标 + "月月工坊台" 展示宋体标题，克制不毁专业感。
 * 反 slop：禁 inset 紫条，禁玻璃发光，单一琥珀强调。 */
import { RouterLink } from 'vue-router';
import {
  LayoutDashboard,
  BarChart3,
  Brain,
  Image as ImageIcon,
  Workflow,
  Video,
  Mic,
  Palette,
  Globe,
  Images,
  BookOpen,
  Search,
  Smile,
  Coins,
  ShieldAlert,
  MessageCircle,
  Gift,
  CalendarCheck,
  Shirt,
  Cog,
  Gem,
} from 'lucide-vue-next';
import type { Component } from 'vue';

interface NavItem {
  to: string;
  label: string;
  icon: Component;
}
interface NavGroup {
  heading: string;
  items: NavItem[];
}

const groups: NavGroup[] = [
  {
    heading: '概览',
    items: [
      { to: '/overview', label: '总览', icon: LayoutDashboard },
      { to: '/stats', label: '统计页', icon: BarChart3 },
    ],
  },
  {
    heading: '模型与生成',
    items: [
      { to: '/ai', label: 'AI 设置', icon: Brain },
      { to: '/imagen', label: '绘图设置', icon: ImageIcon },
      { to: '/comfyui', label: 'ComfyUI 设置', icon: Workflow },
      { to: '/video', label: '视频设置', icon: Video },
      { to: '/voice', label: '语音设置', icon: Mic },
      { to: '/novelai', label: 'NovelAI 设置', icon: Palette },
      { to: '/web-search', label: '网络搜索', icon: Globe },
      { to: '/image-search', label: '图片搜索', icon: Images },
    ],
  },
  {
    heading: '内容与知识',
    items: [
      { to: '/knowledge', label: '知识库', icon: BookOpen },
      { to: '/embedding', label: '向量嵌入', icon: Search },
      { to: '/emoji', label: '表情管理', icon: Smile },
    ],
  },
  {
    heading: '运营与系统',
    items: [
      { to: '/coin', label: '货币设置', icon: Coins },
      { to: '/moderation', label: '管理设置', icon: ShieldAlert },
      { to: '/thread-auto-speaker', label: '自动暖贴', icon: MessageCircle },
      { to: '/spring-festival', label: '新春活动', icon: Gift },
      { to: '/summary', label: '年度总结', icon: CalendarCheck },
      { to: '/daily-outfit', label: '每日换装', icon: Shirt },
      { to: '/system', label: '系统', icon: Cog },
    ],
  },
];
</script>

<template>
  <nav class="sidebar" role="navigation" aria-label="主导航">
    <div class="sidebar__brand">
      <Gem class="sidebar__brand-icon" aria-hidden="true" />
      <div class="sidebar__brand-text">
        <h1 class="sidebar__brand-title font-display">月月工坊台</h1>
        <p class="sidebar__brand-sub">类脑 · Dashboard</p>
      </div>
    </div>

    <div class="sidebar__groups">
      <section v-for="g in groups" :key="g.heading" class="nav-group" role="group" :aria-label="g.heading">
        <p class="nav-group__heading" aria-hidden="true">{{ g.heading }}</p>
        <ul class="nav-group__list">
          <li v-for="item in g.items" :key="item.to">
            <RouterLink :to="item.to" class="nav-item">
              <component :is="item.icon" class="nav-item__icon" :size="18" aria-hidden="true" />
              <span class="nav-item__label">{{ item.label }}</span>
            </RouterLink>
          </li>
        </ul>
      </section>
    </div>
  </nav>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: sticky;
  top: 0;
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  overflow-y: auto;
}

.sidebar__brand {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-5);
  border-bottom: 1px solid var(--border);
}
.sidebar__brand-icon {
  flex: none;
  width: var(--space-6);
  height: var(--space-6);
  color: var(--accent);
}
.sidebar__brand-title {
  font-size: var(--text-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  line-height: var(--lh-tight);
}
.sidebar__brand-sub {
  margin-top: 0.125rem;
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.sidebar__groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  padding: var(--space-5) var(--space-3);
}

.nav-group__heading {
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.nav-group__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  list-style: none;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: var(--fw-regular);
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart),
    border-color var(--dur-micro) var(--ease-out-quart);
}
.nav-item:hover {
  background: var(--bg-surface-2);
  color: var(--text-primary);
  text-decoration: none;
}
.nav-item:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* 激活态：琥珀左边框 2px + 暖底 + 字重提升（禁 inset 紫条） */
.nav-item.router-link-exact-active {
  border-left-color: var(--accent);
  background: var(--accent-subtle);
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
}
.nav-item.router-link-exact-active .nav-item__icon {
  color: var(--accent);
}

.nav-item__icon {
  flex: none;
  color: var(--text-muted);
}
.nav-item__label {
  white-space: nowrap;
}
</style>
