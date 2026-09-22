<script setup lang="ts">
import { ref } from 'vue';
import GameStatsPanel from './GameStatsPanel.vue';
import { soundEnabled, musicEnabled, setSoundEnabled, setMusicEnabled } from './gameAudio';
type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
defineProps<{ profile: { user_id: string; username: string; avatar_url: string; balance: number }; apiCall: ApiCall; gameType?: string; audioOnly?: boolean; chatAvailable?: boolean }>();
const emit = defineEmits<{ chat: [] }>();
const menu = ref<HTMLDialogElement | null>(null);
const panel = ref<'stats' | 'leaderboard' | null>(null);
function openPanel(tab: 'stats' | 'leaderboard') { menu.value?.close(); panel.value = tab; }
</script>

<template>
  <button class="game-button quiet" :aria-label="audioOnly ? '声音设置' : '战绩与声音'" @click="menu?.showModal()">{{ audioOnly ? '声音' : '更多' }}</button>
  <Teleport to="body">
    <dialog ref="menu" class="game-tools-dialog" :aria-label="audioOnly ? '声音设置' : '战绩与声音'">
      <header><h2>{{ audioOnly ? '声音设置' : '战绩与声音' }}</h2><button class="game-button quiet" @click="menu?.close()">关闭</button></header>
      <div v-if="!audioOnly" class="tools-links"><button class="game-button" @click="openPanel('stats')">个人统计</button><button class="game-button" @click="openPanel('leaderboard')">盈利排行</button></div>
      <div v-if="chatAvailable" class="tools-links"><button class="game-button" aria-label="打开牌桌聊天" @click="menu?.close(); emit('chat')">牌桌聊天</button></div>
      <label data-audio-toggle><span>游戏音效与语音<small>点击、加注、快捷聊天与月月语音</small></span><input type="checkbox" :checked="soundEnabled" @change="setSoundEnabled(($event.target as HTMLInputElement).checked)"></label>
      <label data-audio-toggle><span>背景音乐<small>大厅随机音乐 · 对局与胜负音乐</small></span><input type="checkbox" :checked="musicEnabled" @change="setMusicEnabled(($event.target as HTMLInputElement).checked)"></label>
      <p>设置保存在当前设备，切到后台自动静音。</p>
    </dialog>
  </Teleport>
  <GameStatsPanel v-if="panel" :profile="profile" :api-call="apiCall" :initial-tab="panel" :initial-game-type="gameType ?? 'all'" @close="panel = null" />
</template>

<style scoped>
.game-tools-dialog { width: min(420px, calc(var(--activity-width, 100vw) - 24px)); max-height: calc(var(--activity-height, 100dvh) - 24px); box-sizing: border-box; overflow: auto; padding: 18px; margin: auto; border: 2px solid #dbb575; border-radius: 16px; background: #f7e6c8; color: #514565; box-shadow: 0 14px 70px #201b45a0; }
.game-tools-dialog::backdrop { background: #251f4b99; }
header, label, .tools-links { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h2 { margin: 0; font-size: 20px; }
.tools-links { margin: 16px 0; }
.tools-links button { flex: 1; }
.game-tools-dialog button { min-height: 36px; }
label { min-height: 48px; margin-block: 8px; padding: 8px 10px; border: 1px solid #d8b783; border-radius: 8px; background: #fff3dc; cursor: pointer; }
label span { display: grid; gap: 3px; font-size: 14px; }
small, p { color: #786143; font-size: 11px; }
input { width: 22px; height: 22px; accent-color: #556d99; }
</style>
