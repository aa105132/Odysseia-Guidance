import { ref } from 'vue';

function preference(key: string, fallback: boolean) {
  try { const value = localStorage.getItem(key); return value === null ? fallback : value === 'true'; } catch { return fallback; }
}
export const soundEnabled = ref(preference('yueyue:sound', true));
export const musicEnabled = ref(preference('yueyue:music', false));
let context: AudioContext | null = null;
let effects: GainNode | null = null;
let music: GainNode | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let beat = 0;
let nextNote = 0;
let users = 0;
const voices = new Set<OscillatorNode>();

function save(key: string, value: boolean) { try { localStorage.setItem(key, String(value)); } catch { /* 隐私模式仍可使用当次设置。 */ } }
function tone(frequency: number, when: number, length: number, gain: number, bus: GainNode, type: OscillatorType = 'sine') {
  if (!context) return;
  const oscillator = context.createOscillator();
  const envelope = context.createGain();
  oscillator.type = type;
  oscillator.frequency.value = frequency;
  envelope.gain.setValueAtTime(0, when);
  envelope.gain.linearRampToValueAtTime(gain, when + .012);
  envelope.gain.exponentialRampToValueAtTime(.0001, when + length);
  oscillator.connect(envelope).connect(bus);
  voices.add(oscillator);
  oscillator.onended = () => { voices.delete(oscillator); oscillator.disconnect(); envelope.disconnect(); };
  oscillator.start(when);
  oscillator.stop(when + length + .02);
}
function scheduleMusic() {
  if (!context || !music || !musicEnabled.value || document.hidden || context.state !== 'running') return;
  // 原创五声音阶短曲，以轻柔拨弦和低音循环，无外部音频依赖。
  const melody = [60, 64, 67, 69, 67, 64, 62, 64, 67, 72, 69, 67, 64, 62, 60, 0,
    64, 67, 69, 72, 74, 72, 69, 67, 64, 62, 64, 67, 62, 60, 0, 0];
  while (nextNote < context.currentTime + .2) {
    const note = melody[beat % melody.length]!;
    if (note) tone(440 * 2 ** ((note - 69) / 12), nextNote, 1.15, .10, music, 'triangle');
    if (beat % 4 === 0) tone(440 * 2 ** (((beat % 16 < 8 ? 48 : 43) - 69) / 12), nextNote, 2, .07, music);
    beat++;
    nextNote += .48;
  }
  timer = setTimeout(scheduleMusic, 100);
}
function updateMusic() {
  clearTimeout(timer);
  if (!context || !music) return;
  music.gain.setValueAtTime(musicEnabled.value && !document.hidden ? .55 : 0, context.currentTime);
  nextNote = context.currentTime + .03;
  scheduleMusic();
}
async function unlock() {
  if (document.hidden || (!soundEnabled.value && !musicEnabled.value)) return;
  try {
    if (!context) {
      context = new AudioContext();
      effects = context.createGain(); effects.connect(context.destination);
      music = context.createGain(); music.connect(context.destination);
      effects.gain.value = soundEnabled.value ? .32 : 0;
      music.gain.value = musicEnabled.value ? .55 : 0;
    }
    if (context.state !== 'running') { await context.resume(); updateMusic(); }
    else if (musicEnabled.value && timer === undefined) updateMusic();
  } catch { /* 浏览器禁止音频时不影响游戏。 */ }
}
export function setSoundEnabled(enabled: boolean) {
  soundEnabled.value = enabled; save('yueyue:sound', enabled);
  if (context && effects) effects.gain.setValueAtTime(enabled ? .32 : 0, context.currentTime);
  if (enabled) void unlock();
}
export function setMusicEnabled(enabled: boolean) {
  musicEnabled.value = enabled; save('yueyue:music', enabled);
  void unlock().then(updateMusic);
  updateMusic();
}
export function playGameSound(kind: 'click' | 'raise' | 'deal' | 'win' = 'click') {
  if (!soundEnabled.value || !context || !effects || context.state !== 'running' || document.hidden) return;
  const now = context.currentTime;
  const notes = kind === 'raise' ? [660, 880, 1100] : kind === 'win' ? [523, 659, 784] : kind === 'deal' ? [440, 330] : [740];
  notes.forEach((frequency, index) => tone(frequency, now + index * .055, kind === 'click' ? .055 : .16, .14, effects!, 'triangle'));
}
function click(event: MouseEvent) {
  const target = event.target instanceof Element ? event.target.closest('button, input[type="checkbox"]') : null;
  if (!target || target.matches(':disabled') || !event.isTrusted || target.closest('[data-audio-toggle]')) return;
  void unlock().then(() => playGameSound());
}
function visibility() {
  clearTimeout(timer); timer = undefined;
  if (document.hidden) { if (context) void context.suspend().catch(() => {}); }
  else if (context) void unlock().then(updateMusic);
}
export function mountGameAudio() {
  if (++users !== 1) return;
  document.addEventListener('click', click);
  document.addEventListener('visibilitychange', visibility);
}
export function unmountGameAudio() {
  if (--users > 0) return;
  users = 0;
  document.removeEventListener('click', click);
  document.removeEventListener('visibilitychange', visibility);
  clearTimeout(timer); timer = undefined;
  voices.forEach(voice => { try { voice.stop(); } catch { /* 已结束的音符无需处理。 */ } });
  voices.clear();
  if (context) void context.close().catch(() => {});
  context = null; music = null; effects = null;
}
