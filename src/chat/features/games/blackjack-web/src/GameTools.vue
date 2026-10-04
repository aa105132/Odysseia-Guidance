<script setup lang="ts">
import { ref } from 'vue';
import GameStatsPanel from './GameStatsPanel.vue';
import { soundEnabled, voiceEnabled, musicEnabled, soundVolume, voiceVolume, musicVolume, setSoundEnabled, setVoiceEnabled, setMusicEnabled, setSoundVolume, setVoiceVolume, setMusicVolume } from './gameAudio';
import './guochao-dialog.css';
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
    <dialog ref="menu" class="game-tools-dialog gc-dialog" :aria-label="audioOnly ? '声音设置' : '战绩与声音'">
      <header class="gc-heading"><span class="gc-seal" aria-hidden="true">乐</span><div><small>月月茶馆 · 随心而设</small><h2>{{ audioOnly ? '声音设置' : '战绩与声音' }}</h2></div><button class="game-button quiet gc-close" aria-label="关闭" @click="menu?.close()">关闭</button></header>
      <div class="tools-content gc-paper">
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
      </div>
    </dialog>
  </Teleport>
  <GameStatsPanel v-if="panel" :profile="profile" :api-call="apiCall" :initial-tab="panel" :initial-game-type="gameType ?? 'all'" @close="panel = null" />
</template>
