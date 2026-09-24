<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { clientPointToActivity } from './activityViewport';
import { mountGameAudio, playGameVoice, unmountGameAudio } from './gameAudio';
import { yueyueVoiceLines } from './yueyueVoiceLines';

const props = withDefaults(defineProps<{
  message?: string;
  spriteSrc?: string;
}>(), {
  message: '我在呢，今天想玩什么？',
  spriteSrc: '/ui/yueyue/spritesheet.webp?v=20260924',
});
const emit = defineEmits<{ interact: [] }>();

// 与 hatch-pet v2 的九行动作、两行视线及每帧时长保持一致。
const animations = {
  idle: { row: 0, durations: [280, 110, 110, 140, 140, 320] },
  waving: { row: 3, durations: [140, 140, 140, 280] },
  jumping: { row: 4, durations: [140, 140, 140, 140, 280] },
  waiting: { row: 6, durations: [150, 150, 150, 150, 150, 260] },
  review: { row: 8, durations: [150, 150, 150, 150, 150, 280] },
} as const;
type AnimationName = keyof typeof animations;
const interactions: { voice: string; animation: AnimationName }[] = [
  { voice: 'pet_hello', animation: 'waving' },
  { voice: 'pet_touch', animation: 'review' },
  { voice: 'pet_cheer', animation: 'jumping' },
  { voice: 'pet_tea', animation: 'waiting' },
];
const element = ref<HTMLElement>();
const button = ref<HTMLButtonElement>();
const ready = ref(false);
const reducedMotion = ref(false);
const visible = ref(true);
const inView = ref(true);
const animation = ref<AnimationName>('idle');
const frame = ref(0);
const gaze = ref<number | null>(null);
const reply = ref('');
const busy = ref(false);
let interactionIndex = 0;
let frameTimer: ReturnType<typeof setTimeout> | undefined;
let interactionTimer: ReturnType<typeof setTimeout> | undefined;
let speechTimer: ReturnType<typeof setTimeout> | undefined;
let pointerFrame = 0;
let pointer: { x: number; y: number } | null = null;
let media: MediaQueryList | undefined;
let observer: IntersectionObserver | undefined;
let disposed = false;

const currentCell = computed(() => reducedMotion.value
  ? { row: 0, column: 0 }
  : gaze.value === null
    ? { row: animations[animation.value].row, column: frame.value }
    : { row: 9 + Math.floor(gaze.value / 8), column: gaze.value % 8 });
const spriteStyle = computed(() => ({
  backgroundImage: `url(${JSON.stringify(props.spriteSrc)})`,
  backgroundPosition: `${currentCell.value.column / 7 * 100}% ${currentCell.value.row / 10 * 100}%`,
}));
const speech = computed(() => reply.value || props.message);
const motionActive = computed(() => ready.value && visible.value && inView.value && !reducedMotion.value);

function clearFrameTimer() {
  clearTimeout(frameTimer);
  frameTimer = undefined;
}
function scheduleFrame() {
  clearFrameTimer();
  if (disposed || !motionActive.value || gaze.value !== null) return;
  const currentAnimation = animations[animation.value];
  frameTimer = setTimeout(() => {
    frameTimer = undefined;
    if (!motionActive.value || gaze.value !== null) return;
    if (frame.value + 1 < currentAnimation.durations.length) frame.value++;
    else if (animation.value === 'idle') frame.value = 0;
    else return;
    scheduleFrame();
  }, currentAnimation.durations[frame.value] ?? 140);
}
function resetMotion() {
  gaze.value = null;
  animation.value = 'idle';
  frame.value = 0;
  scheduleFrame();
}
function interact() {
  if (busy.value || !visible.value) return;
  emit('interact');
  const interaction = interactions[interactionIndex++ % interactions.length]!;
  busy.value = true;
  gaze.value = null;
  animation.value = interaction.animation;
  frame.value = 0;
  reply.value = yueyueVoiceLines.find(line => line.id === interaction.voice)?.text ?? '';
  void playGameVoice(interaction.voice);
  scheduleFrame();
  // 保留完整动作与对白，连续点按不会不断重启语音。
  clearTimeout(interactionTimer);
  interactionTimer = setTimeout(() => { busy.value = false; resetMotion(); }, 2600);
  clearTimeout(speechTimer);
  speechTimer = setTimeout(() => { reply.value = ''; }, 6200);
}
function loadSprite(event: Event) {
  const image = event.target as HTMLImageElement;
  // 只播放完整 v2 图集，避免把尚未完成的九行图集切错帧。
  ready.value = image.naturalWidth === 1536 && image.naturalHeight === 2288;
  scheduleFrame();
}
function updateGaze() {
  pointerFrame = 0;
  if (!pointer || !button.value || !motionActive.value || busy.value) return;
  const box = button.value.getBoundingClientRect();
  const center = clientPointToActivity(box.left + box.width / 2, box.top + box.height / 2);
  const point = clientPointToActivity(pointer.x, pointer.y);
  const dx = point.x - center.x, dy = point.y - center.y;
  if (Math.hypot(dx, dy) < Math.max(24, button.value.offsetWidth * .22)) {
    if (gaze.value !== null) { gaze.value = null; scheduleFrame(); }
    return;
  }
  // 零度为正上方；指针坐标先转换到活动坐标，以兼容手机自动旋转。
  const angle = (Math.atan2(dx, -dy) + Math.PI * 2) % (Math.PI * 2);
  gaze.value = Math.round(angle / (Math.PI / 8)) % 16;
  clearFrameTimer();
}
function followPointer(event: PointerEvent) {
  if (event.pointerType !== 'mouse' || !motionActive.value || busy.value) return;
  pointer = { x: event.clientX, y: event.clientY };
  if (!pointerFrame) pointerFrame = requestAnimationFrame(updateGaze);
}
function leavePointer() {
  pointer = null;
  if (pointerFrame) cancelAnimationFrame(pointerFrame);
  pointerFrame = 0;
  if (gaze.value !== null) { gaze.value = null; scheduleFrame(); }
}
function updateVisibility() {
  visible.value = !document.hidden;
  leavePointer();
  scheduleFrame();
}
function updateReducedMotion() {
  reducedMotion.value = media?.matches ?? false;
  gaze.value = null;
  frame.value = 0;
  scheduleFrame();
}

watch(() => props.spriteSrc, () => { ready.value = false; resetMotion(); });
onMounted(() => {
  mountGameAudio();
  media = matchMedia('(prefers-reduced-motion: reduce)');
  updateReducedMotion();
  updateVisibility();
  media.addEventListener('change', updateReducedMotion);
  document.addEventListener('pointermove', followPointer, { passive: true });
  document.addEventListener('pointerleave', leavePointer);
  document.addEventListener('visibilitychange', updateVisibility);
  window.addEventListener('blur', leavePointer);
  if ('IntersectionObserver' in window && element.value) {
    observer = new IntersectionObserver(entries => {
      inView.value = entries.some(entry => entry.isIntersecting);
      if (!inView.value) leavePointer();
      scheduleFrame();
    });
    observer.observe(element.value);
  }
});
onBeforeUnmount(() => {
  disposed = true;
  clearFrameTimer();
  clearTimeout(interactionTimer);
  clearTimeout(speechTimer);
  if (pointerFrame) cancelAnimationFrame(pointerFrame);
  observer?.disconnect();
  media?.removeEventListener('change', updateReducedMotion);
  document.removeEventListener('pointermove', followPointer);
  document.removeEventListener('pointerleave', leavePointer);
  document.removeEventListener('visibilitychange', updateVisibility);
  window.removeEventListener('blur', leavePointer);
  unmountGameAudio();
});
</script>

<template>
  <aside ref="element" class="yueyue-mascot" aria-label="月月陪你玩">
    <button
      ref="button" class="yueyue-mascot-button" type="button" aria-label="和月月打招呼"
      :aria-busy="busy" @click="interact"
    >
      <span
        v-if="ready" class="yueyue-mascot-sprite" :style="spriteStyle" aria-hidden="true"
        :data-animation="gaze === null ? animation : 'look'" :data-row="currentCell.row" :data-frame="currentCell.column"
      />
      <img v-else src="/character/normal.webp" class="yueyue-mascot-fallback" alt="" draggable="false">
      <span class="yueyue-mascot-hint" aria-hidden="true">点我聊聊</span>
    </button>
    <div class="yueyue-mascot-speech">
      <span class="yueyue-mascot-name">月月</span>
      <p aria-live="polite" aria-atomic="true">{{ speech }}</p>
    </div>
    <img class="yueyue-mascot-preload" :src="spriteSrc" alt="" aria-hidden="true" @load="loadSprite" @error="ready = false">
  </aside>
</template>

<style scoped>
.yueyue-mascot { display: flex; flex: 0 0 auto; flex-direction: row-reverse; align-items: center; justify-content: flex-start; gap: 8px; width: 100%; max-width: 1120px; margin: auto auto 0; min-height: 0; color: #4b5661; pointer-events: none; }
.yueyue-mascot-button { --mascot-width: clamp(96px, 12vw, 154px); position: relative; flex: 0 0 auto; display: block; width: var(--mascot-width); height: calc(var(--mascot-width) * 208 / 192); padding: 0; border: 0; border-radius: 18px; background: none; box-shadow: none; color: inherit; pointer-events: auto; user-select: none; -webkit-user-select: none; touch-action: manipulation; }
.yueyue-mascot-button:focus-visible { outline: 2px solid #877558; outline-offset: 3px; background: #faf7ed66; }
.yueyue-mascot-sprite { display: block; width: 100%; height: 100%; background-repeat: no-repeat; background-size: 800% 1100%; }
.yueyue-mascot-fallback { width: 100%; height: 100%; object-fit: contain; pointer-events: none; }
.yueyue-mascot-hint { position: absolute; bottom: 2px; left: 50%; transform: translateX(-50%); padding: 3px 8px; border: 1px solid #c5baaa; border-radius: 12px; background: #f8f3e6ee; color: #6d6456; font-size: 10px; line-height: 1.2; white-space: nowrap; opacity: 0; transition: opacity .15s; }
.yueyue-mascot-button:hover .yueyue-mascot-hint, .yueyue-mascot-button:focus-visible .yueyue-mascot-hint { opacity: 1; }
.yueyue-mascot-speech { max-width: 350px; min-width: 0; padding: 11px 16px; border: 1px solid #cdc5b7; border-radius: 14px 14px 3px 14px; background: #f8f5edeb; box-shadow: 0 3px 12px #484b3e0b; }
.yueyue-mascot-name { display: block; margin-bottom: 3px; color: #877256; font-size: 10px; font-weight: 600; letter-spacing: .12em; }
.yueyue-mascot-speech p { margin: 0; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.yueyue-mascot-preload { display: none; }
@media (pointer: coarse) { .yueyue-mascot-hint { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .yueyue-mascot-hint { transition: none; } }
@container activity-viewport (max-height: 560px) {
  .yueyue-mascot { gap: 6px; }
  .yueyue-mascot-button { --mascot-width: 76px; }
  .yueyue-mascot-speech { max-width: 310px; padding: 7px 12px; }
  .yueyue-mascot-name { margin-bottom: 1px; font-size: 9px; }
  .yueyue-mascot-speech p { font-size: 11px; line-height: 1.5; }
  .yueyue-mascot-hint { padding: 2px 6px; font-size: 9px; }
}
</style>
