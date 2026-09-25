<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { FARM_ATLASES, type FarmAnimation } from './farmAtlas';
const props = withDefaults(defineProps<{ animation?: FarmAnimation; src?: string; frames?: number; frame?: number; duration?: number; loop?: boolean; paused?: boolean; fallback?: string; label?: string }>(), { frames: 8, loop: false, paused: false, label: '' });
const emit = defineEmits<{ ready: [] }>();
const failed = ref(false);
const loaded = ref(false);
const atlas = computed(() => props.animation ? FARM_ATLASES[props.animation] : undefined);
const source = computed(() => props.src || atlas.value?.src || '');
const frameCount = computed(() => atlas.value?.frames ?? props.frames);
const style = computed(() => ({ '--sprite-frames': frameCount.value, '--sprite-steps': frameCount.value - 1, '--sprite-duration': `${props.duration ?? atlas.value?.duration ?? 1800}ms`, '--sprite-last': `${-(frameCount.value - 1) / frameCount.value * 100}%`, '--sprite-frame': `${-(props.frame ?? 0) / frameCount.value * 100}%` }));
watch(source, () => { failed.value = false; loaded.value = false; });
</script>
<template>
  <span class="farm-sprite" :class="{ 'sprite-loop': loop, 'sprite-still': frame !== undefined, 'sprite-paused': paused, 'sprite-loaded': loaded }" :style="style" :data-animation="animation" :role="label ? 'img' : undefined" :aria-label="label || undefined" :aria-hidden="label ? undefined : true">
    <img v-if="!failed && source" class="sprite-strip" :src="source" alt="" draggable="false" @load="loaded = true; emit('ready')" @error="failed = true; loaded = false">
    <img v-if="fallback && (failed || !loaded)" class="sprite-fallback" :src="fallback" alt="" draggable="false">
  </span>
</template>
<style scoped>
.farm-sprite { position: relative; display: block; aspect-ratio: 1; overflow: hidden; pointer-events: none; }
.sprite-strip { display: block; width: calc(var(--sprite-frames) * 100%); max-width: none; height: 100%; object-fit: fill; visibility: hidden; transform: translateX(0); }
.sprite-loaded .sprite-strip { visibility: visible; animation: farm-sprite-once var(--sprite-duration) steps(var(--sprite-steps), end) both; }
.sprite-loaded.sprite-loop .sprite-strip { animation: farm-sprite-loop var(--sprite-duration) steps(var(--sprite-frames), end) infinite; }
.sprite-loaded.sprite-still .sprite-strip { animation: none; transform: translateX(var(--sprite-frame)); }
.sprite-paused .sprite-strip { animation-play-state: paused !important; }
.sprite-fallback { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
@keyframes farm-sprite-once { to { transform: translateX(var(--sprite-last)); } }
@keyframes farm-sprite-loop { to { transform: translateX(-100%); } }
@media (prefers-reduced-motion: reduce) { .sprite-loaded .sprite-strip, .sprite-loaded.sprite-loop .sprite-strip { animation: none; transform: translateX(var(--sprite-last)); }.sprite-loaded.sprite-still .sprite-strip { transform: translateX(var(--sprite-frame)); } }
</style>
