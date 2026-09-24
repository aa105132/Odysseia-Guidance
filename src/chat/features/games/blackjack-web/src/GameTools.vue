<script setup lang="ts">
import { ref } from 'vue';
import GameStatsPanel from './GameStatsPanel.vue';
import { soundEnabled, voiceEnabled, musicEnabled, soundVolume, voiceVolume, musicVolume, setSoundEnabled, setVoiceEnabled, setMusicEnabled, setSoundVolume, setVoiceVolume, setMusicVolume } from './gameAudio';
type ApiCall = <T>(endpoint: string, method: 'GET' | 'POST', body?: unknown, retries?: number) => Promise<T>;
defineProps<{ profile: { user_id: string; username: string; avatar_url: string; balance: number }; apiCall: ApiCall; gameType?: string; audioOnly?: boolean; rulesAvailable?: boolean }>();
const emit = defineEmits<{ rules: [] }>();
const menu = ref<HTMLDialogElement | null>(null);
const panel = ref<'stats' | 'leaderboard' | null>(null);
function openPanel(tab: 'stats' | 'leaderboard') { menu.value?.close(); panel.value = tab; }
</script>

<template>
  <button class="game-button quiet" :aria-label="audioOnly ? '声音设置' : '战绩与声音'" @click="menu?.showModal()">{{ audioOnly ? '声音' : '更多' }}</button>
  <Teleport to="#app">
    <dialog ref="menu" class="game-tools-dialog" :aria-label="audioOnly ? '声音设置' : '战绩与声音'">
      <header><h2>{{ audioOnly ? '声音设置' : '战绩与声音' }}</h2><button class="game-button quiet" @click="menu?.close()">关闭</button></header>
      <div v-if="!audioOnly" class="tools-links"><button class="game-button" @click="openPanel('stats')">个人统计</button><button class="game-button" @click="openPanel('leaderboard')">盈利排行</button></div>
      <div v-if="rulesAvailable" class="tools-links"><button class="game-button" @click="menu?.close(); emit('rules')">玩法规则</button></div>
      <section class="audio-group">
        <label class="audio-toggle" data-audio-toggle><span>游戏音效<small>点击、加注与发牌提示</small></span><input type="checkbox" :checked="soundEnabled" @change="setSoundEnabled(($event.target as HTMLInputElement).checked)"></label>
        <label class="audio-volume"><span>音效音量<span class="volume-value" aria-hidden="true">{{ Math.round(soundVolume * 100) }}%</span></span><input type="range" min="0" max="100" step="1" :value="Math.round(soundVolume * 100)" :disabled="!soundEnabled" :aria-valuetext="`${Math.round(soundVolume * 100)}%`" @input="setSoundVolume(Number(($event.target as HTMLInputElement).value) / 100)"></label>
      </section>
      <section class="audio-group">
        <label class="audio-toggle" data-audio-toggle><span>月月与聊天语音<small>月月说话、出牌与快捷聊天</small></span><input type="checkbox" :checked="voiceEnabled" @change="setVoiceEnabled(($event.target as HTMLInputElement).checked)"></label>
        <label class="audio-volume"><span>语音音量<span class="volume-value" aria-hidden="true">{{ Math.round(voiceVolume * 100) }}%</span></span><input type="range" min="0" max="100" step="1" :value="Math.round(voiceVolume * 100)" :disabled="!voiceEnabled" :aria-valuetext="`${Math.round(voiceVolume * 100)}%`" @input="setVoiceVolume(Number(($event.target as HTMLInputElement).value) / 100)"></label>
      </section>
      <section class="audio-group">
        <label class="audio-toggle" data-audio-toggle><span>背景音乐<small>大厅随机音乐 · 对局与胜负音乐</small></span><input type="checkbox" :checked="musicEnabled" @change="setMusicEnabled(($event.target as HTMLInputElement).checked)"></label>
        <label class="audio-volume"><span>音乐音量<span class="volume-value" aria-hidden="true">{{ Math.round(musicVolume * 100) }}%</span></span><input type="range" min="0" max="100" step="1" :value="Math.round(musicVolume * 100)" :disabled="!musicEnabled" :aria-valuetext="`${Math.round(musicVolume * 100)}%`" @input="setMusicVolume(Number(($event.target as HTMLInputElement).value) / 100)"></label>
      </section>
      <p>设置保存在当前设备，切到后台自动静音。</p>
    </dialog>
  </Teleport>
  <GameStatsPanel v-if="panel" :profile="profile" :api-call="apiCall" :initial-tab="panel" :initial-game-type="gameType ?? 'all'" @close="panel = null" />
</template>

<style scoped>
.game-tools-dialog { width: min(420px, calc(var(--activity-width, 100vw) - 24px)); max-height: calc(var(--activity-height, 100dvh) - 24px); box-sizing: border-box; overflow: auto; padding: 18px; margin: auto; border: 1px solid #b6a58b; border-radius: 16px; background: #eee7d9; color: #35434e; box-shadow: 0 14px 70px #201b45a0; }
.game-tools-dialog::backdrop { background: #251f4b99; }
header, label, .tools-links { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h2 { margin: 0; font-size: 20px; }
.tools-links { margin: 16px 0; }
.tools-links button { flex: 1; }
.game-tools-dialog button { min-height: 36px; }
.audio-group { margin-top: 12px; padding: 6px 12px 10px; border: 1px solid #c6b9a1; border-radius: 10px; background: #f8f3e9; }
.audio-toggle { min-height: 48px; padding-block: 4px; cursor: pointer; }
label span { display: grid; gap: 3px; font-size: 14px; }
small, p { color: #786143; font-size: 11px; }
input[type="checkbox"] { flex: none; width: 22px; height: 22px; accent-color: #556d99; }
.audio-volume { display: block; margin-top: 6px; }
.audio-volume > span { display: flex; align-items: center; justify-content: space-between; font-size: 12px; }
.audio-volume .volume-value { color: #786143; font-variant-numeric: tabular-nums; }
input[type="range"] { display: block; width: 100%; height: 28px; margin: 2px 0 0; padding: 0; cursor: pointer; accent-color: #556d99; }
input[type="range"]:disabled { cursor: default; opacity: .45; }
</style>
