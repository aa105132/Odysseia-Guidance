import { ref } from 'vue';
import { gameVoiceIds } from './gameVoiceLines';

function preference(key: string, fallback: boolean) {
  try { const value = localStorage.getItem(key); return value === null ? fallback : value === 'true'; } catch { return fallback; }
}
export const soundEnabled = ref(preference('yueyue:sound', true));
export const musicEnabled = ref(preference('yueyue:music', false));
function initialVoicePreference() {
  const enabled = preference('yueyue:voice', soundEnabled.value);
  // 首次升级沿用旧的总静音状态；随后音效与语音分别保存，互不反向覆盖。
  save('yueyue:voice', enabled);
  return enabled;
}
export const voiceEnabled = ref(initialVoicePreference());
function volumePreference(key: string, fallback: number) {
  try {
    const stored = localStorage.getItem(key);
    const value = stored === null || stored.trim() === '' ? NaN : Number(stored);
    return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : fallback;
  } catch { return fallback; }
}
export const soundVolume = ref(volumePreference('yueyue:sound-volume', .32));
export const voiceVolume = ref(volumePreference('yueyue:voice-volume', .9));
export const musicVolume = ref(volumePreference('yueyue:music-volume', .36));
let context: AudioContext | null = null;
let effects: GainNode | null = null;
let users = 0;
let unlocked = false;
let scene: 'lobby' | 'playing' = 'lobby';
let musicTrack: HTMLAudioElement | null = null;
let voiceTrack: HTMLAudioElement | null = null;
let musicName = '';
let previousLobby = '';
let roundInterlude = false;
let voiceEpoch = 0;
let musicEpoch = 0;
const voices = new Set<OscillatorNode>();
const lobbyTracks = ['Exciting1', 'Exciting2', 'lobby'];

function save(key: string, value: boolean | number) { try { localStorage.setItem(key, String(value)); } catch { /* 隐私模式仍可使用当次设置。 */ } }
function release(track: HTMLAudioElement | null) {
  if (!track) return;
  track.pause(); track.onended = null; track.onerror = null; track.removeAttribute('src'); track.load();
}
function syncMusicVolume() {
  // 语音响起时按用户音量同比压低配乐，结束后恢复，避免覆盖手动设置。
  if (musicTrack) musicTrack.volume = musicVolume.value * (voiceTrack ? .10 / .36 : 1);
}
function normalizedVolume(value: number) { return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0; }
function syncSoundVolume() {
  if (effects) effects.gain.value = soundEnabled.value ? soundVolume.value : 0;
}
export function setSoundVolume(value: number) {
  soundVolume.value = normalizedVolume(value); save('yueyue:sound-volume', soundVolume.value);
  syncSoundVolume();
}
export function setVoiceVolume(value: number) {
  voiceVolume.value = normalizedVolume(value); save('yueyue:voice-volume', voiceVolume.value);
  if (!voiceVolume.value) stopGameVoice();
  else if (voiceTrack) voiceTrack.volume = voiceVolume.value;
}
export function setMusicVolume(value: number) {
  musicVolume.value = normalizedVolume(value); save('yueyue:music-volume', musicVolume.value);
  syncMusicVolume();
}
function tone(frequency: number, when: number, length: number, gain: number, bus: GainNode, type: OscillatorType = 'sine') {
  if (!context) return;
  const oscillator = context.createOscillator();
  const envelope = context.createGain();
  oscillator.type = type; oscillator.frequency.value = frequency;
  envelope.gain.setValueAtTime(0, when); envelope.gain.linearRampToValueAtTime(gain, when + .012);
  envelope.gain.exponentialRampToValueAtTime(.0001, when + length);
  oscillator.connect(envelope).connect(bus); voices.add(oscillator);
  oscillator.onended = () => { voices.delete(oscillator); oscillator.disconnect(); envelope.disconnect(); };
  oscillator.start(when); oscillator.stop(when + length + .02);
}
function selectSceneMusic() {
  if (scene === 'playing') return 'Normal';
  const candidates = lobbyTracks.filter(track => track !== previousLobby);
  const track = candidates[Math.floor(Math.random() * candidates.length)]!;
  previousLobby = track;
  return track;
}
function playMusic(name: string, interlude = false) {
  if (!unlocked || !musicEnabled.value || document.hidden || !users) return;
  const epoch = ++musicEpoch;
  release(musicTrack);
  musicTrack = new Audio(`/audio/music/${name}.mp3`);
  musicName = name; roundInterlude = interlude;
  musicTrack.loop = !interlude && name === 'Normal';
  syncMusicVolume();
  musicTrack.onended = () => { if (epoch === musicEpoch) { roundInterlude = false; playMusic(selectSceneMusic()); } };
  musicTrack.onerror = () => { if (epoch === musicEpoch) { release(musicTrack); musicTrack = null; musicName = ''; roundInterlude = false; } };
  void musicTrack.play().catch(() => { /* 浏览器尚未允许播放时，等待下一次真实手势。 */ });
}
function updateMusic() {
  if (!musicEnabled.value || document.hidden || !unlocked || !users) { musicTrack?.pause(); return; }
  if (!musicTrack || (!roundInterlude && ((scene === 'playing') !== (musicName === 'Normal')))) playMusic(selectSceneMusic());
  else { syncMusicVolume(); void musicTrack.play().catch(() => {}); }
}
async function unlock(fromGesture = false) {
  if (document.hidden || (!soundEnabled.value && !voiceEnabled.value && !musicEnabled.value)) return;
  if (fromGesture || (typeof navigator !== 'undefined' && navigator.userActivation?.isActive)) unlocked = true;
  if (!unlocked) return;
  try {
    if (!context) { context = new AudioContext(); effects = context.createGain(); effects.connect(context.destination); }
    syncSoundVolume();
    if (context.state !== 'running') await context.resume();
  } catch { /* 浏览器不支持合成音效时仍允许媒体音乐。 */ }
  updateMusic();
}
export function setSoundEnabled(enabled: boolean) {
  soundEnabled.value = enabled; save('yueyue:sound', enabled);
  syncSoundVolume();
  if (enabled) void unlock();
}
export function setVoiceEnabled(enabled: boolean) {
  voiceEnabled.value = enabled; save('yueyue:voice', enabled);
  if (!enabled) stopGameVoice();
  else void unlock();
}
export function setMusicEnabled(enabled: boolean) {
  musicEnabled.value = enabled; save('yueyue:music', enabled);
  if (enabled) void unlock();
  updateMusic();
}
export function setGameAudioScene(next: 'lobby' | 'playing') {
  // 新局可能仍属于playing场景，也必须停止上一局的胜负插曲。
  if (scene === next && !roundInterlude) return;
  scene = next;
  if (roundInterlude) {
    musicEpoch++; release(musicTrack); musicTrack = null; musicName = ''; roundInterlude = false;
  }
  updateMusic();
}
export function playRoundMusic(result: 'win' | 'loss' | 'push') {
  if (result !== 'push') playMusic(result === 'win' ? 'win' : 'lose', true);
}
export function stopGameVoice() {
  voiceEpoch++; release(voiceTrack); voiceTrack = null; syncMusicVolume();
}
export async function playGameVoice(id: string) {
  if (!voiceEnabled.value || !voiceVolume.value || document.hidden || !gameVoiceIds.has(id) || !users) return;
  if (!unlocked) return;
  stopGameVoice();
  const epoch = voiceEpoch;
  const track = new Audio(`/audio/voice/${id}.mp3?v=doubao-20260924`);
  voiceTrack = track; track.volume = voiceVolume.value; syncMusicVolume();
  const finish = () => { if (epoch === voiceEpoch) { release(voiceTrack); voiceTrack = null; syncMusicVolume(); } };
  track.onended = finish; track.onerror = finish;
  try { await track.play(); } catch { finish(); }
}
export function playGameSound(kind: 'click' | 'raise' | 'deal' | 'win' = 'click') {
  if (!soundEnabled.value || !soundVolume.value || !context || !effects || context.state !== 'running' || document.hidden) return;
  const now = context.currentTime;
  const notes = kind === 'raise' ? [660, 880, 1100] : kind === 'win' ? [523, 659, 784] : kind === 'deal' ? [440, 330] : [740];
  notes.forEach((frequency, index) => tone(frequency, now + index * .055, kind === 'click' ? .055 : .16, .14, effects!, 'triangle'));
}
function click(event: MouseEvent) {
  if (!event.isTrusted) return;
  const target = event.target instanceof Element ? event.target.closest('button, input[type="checkbox"]') : null;
  void unlock(true).then(() => { if (target && !target.matches(':disabled') && !target.closest('[data-audio-toggle]')) playGameSound(); });
}
function visibility() {
  if (document.hidden) { musicTrack?.pause(); stopGameVoice(); if (context) void context.suspend().catch(() => {}); }
  else if (unlocked) void unlock();
}
export function mountGameAudio() {
  if (++users !== 1) return;
  document.addEventListener('click', click, true); document.addEventListener('visibilitychange', visibility);
}
export function unmountGameAudio() {
  if (--users > 0) return;
  users = 0; unlocked = false; musicEpoch++;
  document.removeEventListener('click', click, true); document.removeEventListener('visibilitychange', visibility);
  stopGameVoice(); release(musicTrack); musicTrack = null; musicName = ''; roundInterlude = false;
  voices.forEach(voice => { try { voice.stop(); } catch { /* 已结束的音符无需处理。 */ } }); voices.clear();
  if (context) void context.close().catch(() => {});
  context = null; effects = null;
}
