<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import FarmSprite from './FarmSprite.vue';
const props = withDefaults(defineProps<{ name?: string; icon?: string; stage?: 'seed' | 'sprout' | 'grown'; quality?: string | null; paused?: boolean }>(), { name: '', icon: '', stage: 'grown', quality: 'normal' });
const paintedLoaded = ref(false);
const paintedFailed = ref(false);
const paintedSource = computed(() => /^[a-z_]+$/.test(props.icon) ? `/ui/farm/plants/${props.icon}.webp` : '');
watch(paintedSource, () => { paintedLoaded.value = false; paintedFailed.value = false; });
const shape = computed(() => {
  const key = `${props.name} ${props.icon}`;
  if (/芝|蘑|菇|mushroom|zhi/i.test(key)) return 'mushroom';
  if (/竹|bamboo/i.test(key)) return 'bamboo';
  if (/莲|lotus/i.test(key)) return 'lotus';
  if (/参|ginseng/i.test(key)) return 'ginseng';
  if (/果|fruit/i.test(key)) return 'fruit';
  if (/藤|vine/i.test(key)) return 'vine';
  return 'herb';
});
</script>

<template>
  <span class="farm-plant" :class="[`plant-${shape}`, `plant-${stage}`, { 'plant-mutated': quality && quality !== 'normal', 'plant-paused': paused }]" aria-hidden="true">
  <FarmSprite v-if="stage !== 'grown'" :key="stage" class="painted-stage" src="/ui/farm-v2/growth-stages.webp" :frame="stage === 'seed' ? 0 : 3" :paused="paused" />
  <img v-if="paintedSource && stage === 'grown' && !paintedFailed" class="painted-plant" :class="{ loaded: paintedLoaded }" :src="paintedSource" alt="" draggable="false" @load="paintedLoaded = true" @error="paintedFailed = true; paintedLoaded = false">
  <svg v-show="!paintedLoaded || stage !== 'grown'" class="plant-fallback" viewBox="0 0 160 140" focusable="false">
    <ellipse cx="80" cy="123" rx="48" ry="8" fill="#193c28" opacity=".16" />
    <g v-if="stage === 'seed'" class="plant-body"><path d="M79 105c-14-19 6-24 13-10 5 12-7 19-13 10Z" fill="#d4a55e" stroke="#956d37" stroke-width="3"/><path d="m84 98-1 10" stroke="#f1d9a4" stroke-width="2" /></g>
    <g v-else-if="stage === 'sprout'" class="plant-body"><path d="M79 121V91" stroke="#427446" stroke-width="6" stroke-linecap="round"/><path d="M79 102c-24 3-34-12-33-22 21-4 32 7 33 22Z" fill="#79a45e" stroke="#527c48" stroke-width="2"/><path d="M80 91c-4-19 12-31 27-29 4 17-7 28-27 29Z" fill="#98b873" stroke="#638b51" stroke-width="2"/></g>
    <g v-else class="plant-body">
      <g v-if="shape === 'mushroom'">
        <path d="M80 117c7-15 5-27-2-39l17-6c1 16-4 28 5 43Z" fill="#ead4a5" stroke="#b08a53" stroke-width="2"/>
        <path d="M41 75c-1-15 16-25 27-29 19-7 46-4 57 15 9 16-9 25-33 26-23 2-45 0-51-12Z" fill="#b3643f" stroke="#824b36" stroke-width="3"/>
        <path d="M46 68c15-22 56-27 76-6M50 76c20 9 41 8 64 0M60 64c18-13 38-14 53-5" fill="none" stroke="#e4aa6b" stroke-width="3" stroke-linecap="round"/>
        <path d="M52 124c7-13 7-22 1-30l12-4c1 13-1 22 5 32" fill="#e4cea1"/><path d="M29 94c3-20 35-27 49-10 11 13-12 20-32 19-12 0-18-3-17-9Z" fill="#ca8150" stroke="#915535" stroke-width="2"/><path d="M35 94c11 4 27 3 37-3" stroke="#efc185" stroke-width="2" fill="none"/>
      </g>
      <g v-else-if="shape === 'bamboo'">
        <path d="m70 123 5-103m17 103 10-83" stroke="#66916a" stroke-width="12" stroke-linecap="round"/><path d="m70 97 9 1m-8-28 9 1m-8-27 9 1m13 57 10 2m-7-31 10 2" stroke="#b5c895" stroke-width="3"/>
        <path d="M77 48c-20-3-36-13-45-26 25 0 39 11 45 26Zm1 25c19-4 35-14 42-30-25 5-39 15-42 30Zm24 23c20-1 36-7 47-20-23-3-39 5-47 20ZM72 98C50 90 36 77 29 61c27 7 40 21 43 37Z" fill="#537e56"/>
      </g>
      <g v-else-if="shape === 'lotus'">
        <path d="M80 120V82" stroke="#608657" stroke-width="5"/><ellipse cx="80" cy="116" rx="48" ry="13" fill="#709a69"/><path d="m80 117-15-13m15 13 22-9" stroke="#b1c28e" stroke-width="2"/>
        <path d="M80 92c-35-1-52-24-42-43 19 5 33 16 42 43Z" fill="#d997a0" stroke="#a57081" stroke-width="2"/><path d="M80 92c35-1 52-24 42-43-19 5-33 16-42 43Z" fill="#e6b0b7" stroke="#b0798b" stroke-width="2"/>
        <path d="M80 87C50 72 60 40 80 24c20 16 30 48 0 63Z" fill="#f1d6d1" stroke="#c38d9b" stroke-width="2"/><path d="M80 93c-21-3-32-15-30-28 17 2 27 9 30 28Zm0 0c21-3 32-15 30-28-17 2-27 9-30 28Z" fill="#efc3bd" stroke="#bd8191" stroke-width="2"/><ellipse cx="80" cy="90" rx="10" ry="4" fill="#e1bd69"/>
      </g>
      <g v-else-if="shape === 'ginseng'">
        <path d="M81 59c-12 16-16 37-9 45l-10 20m21-24 7 25m-19-29-17 12m37-15 17 12" fill="#dcb783" stroke="#b68e59" stroke-width="5" stroke-linecap="round"/>
        <path d="M80 62V38" stroke="#4d784f" stroke-width="5"/><path d="M80 46C61 20 45 20 31 30c12 19 32 26 49 16Zm0 0c19-26 35-26 49-16-12 19-32 26-49 16Zm0-5c-10-14-9-27 0-35 12 11 13 23 0 35Z" fill="#648c56" stroke="#476e49" stroke-width="2"/><circle cx="69" cy="60" r="6" fill="#b65c55"/><circle cx="81" cy="57" r="7" fill="#ce7660"/><circle cx="91" cy="62" r="5" fill="#a4534e"/>
      </g>
      <g v-else>
        <path d="M80 122c-2-28 4-57-1-92" fill="none" stroke="#52784c" stroke-width="5" stroke-linecap="round"/>
        <path d="M80 109c-27 1-45-11-47-27 24-3 41 7 47 27Zm0-20c26 0 42-13 44-29-23-2-40 10-44 29ZM81 67c-23-1-35-14-34-31 22 0 35 13 34 31Zm0-19c22-2 32-18 29-32-21 3-31 17-29 32Z" fill="#709560" stroke="#50794c" stroke-width="2"/>
        <path d="m39 89 35 18m43-41L86 85m-34-43 23 22" stroke="#bdd092" stroke-width="2" stroke-linecap="round"/>
        <g v-if="shape === 'fruit'"><circle cx="54" cy="72" r="14" fill="#cd9355" stroke="#a56c40" stroke-width="2"/><circle cx="103" cy="98" r="12" fill="#dba864" stroke="#ac793f" stroke-width="2"/><path d="m52 58 6-5m45 33 5-5" stroke="#588456" stroke-width="4"/></g>
        <g v-else-if="shape === 'vine'"><path d="M81 112c49-13 20-24 10-6s18 29 37 7" fill="none" stroke="#698c55" stroke-width="3"/><circle cx="74" cy="27" r="8" fill="#c2b389"/><circle cx="84" cy="21" r="8" fill="#ddd1aa"/></g>
        <g v-else><path d="M80 32C53 26 58 8 74 15c6-18 20-9 18 4 20-7 22 12 3 15 6 18-14 23-15 8-18 11-28-5-13-10" fill="#d9c4a0" stroke="#ae9472" stroke-width="2"/><circle cx="82" cy="30" r="7" fill="#d0a94e"/></g>
      </g>
    </g>
    <g v-if="quality === 'spirit' || quality === 'celestial'" class="plant-sparkles" :fill="quality === 'celestial' ? '#edc866' : '#93d4d2'"><path d="m26 35 3-8 3 8 8 3-8 3-3 8-3-8-8-3Z"/><path d="m129 75 2-6 2 6 6 2-6 2-2 6-2-6-6-2Z"/><circle cx="117" cy="22" r="3"/><circle cx="34" cy="106" r="2"/></g>
  </svg>
  <span v-if="paintedLoaded && stage === 'grown' && quality && quality !== 'normal'" class="painted-sparkles" :class="`sparkles-${quality}`">✦<i>✦</i><b>✧</b></span>
  </span>
</template>

<style scoped>
.painted-stage { animation: stage-emerge .9s ease-out both; transform-origin: center 85%; position: absolute; inset: 0; z-index: 1; }.painted-stage.sprite-loaded ~ .plant-fallback { visibility: hidden; }.plant-paused * { animation-play-state: paused !important; }
.farm-plant { position: relative; display: block; width: 100%; height: 100%; overflow: visible; }
.plant-fallback { display: block; width: 100%; height: 100%; overflow: visible; }
.painted-plant { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; visibility: hidden; transform-origin: center 90%; animation: plant-breeze 5.5s ease-in-out infinite; filter: drop-shadow(0 3px 2px #39513b20); }.painted-plant.loaded { visibility: visible; }
.painted-sparkles { position: absolute; left: 13%; top: 22%; color: #8bbbbb; font-size: 15px; text-shadow: 0 0 8px #e7fff1; animation: plant-shimmer 2.8s ease-in-out infinite alternate; }.painted-sparkles i { position: absolute; left: 72px; top: 37px; font-size: 10px; font-style: normal; }.painted-sparkles b { position: absolute; left: 59px; top: -14px; font-size: 12px; font-weight: 400; }.sparkles-celestial { color: #c9a255; text-shadow: 0 0 9px #fff3b3; }
.plant-body { transform-origin: 80px 124px; animation: plant-breeze 4.8s ease-in-out infinite; }
.plant-mushroom .plant-body { animation-duration: 6s; }
.plant-sparkles { animation: plant-shimmer 2.8s ease-in-out infinite alternate; }
@keyframes stage-emerge { from { opacity: .2; transform: scale(.78) translateY(8px); } to { opacity: 1; transform: scale(1) translateY(0); } }
@keyframes plant-breeze { 0%, 100% { transform: rotate(-1.2deg); } 50% { transform: rotate(1.2deg); } }
@keyframes plant-shimmer { from { opacity: .35; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .plant-body, .plant-sparkles, .painted-plant, .painted-sparkles, .painted-stage { animation: none; } }
</style>
