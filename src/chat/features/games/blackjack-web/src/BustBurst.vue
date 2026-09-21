<script setup lang="ts">
withDefaults(defineProps<{ animated?: boolean }>(), { animated: true });
</script>

<template>
  <div v-if="animated" class="bust-burst" data-bust-burst="true" aria-hidden="true">
    <span class="bust-sprite" />
    <svg viewBox="0 0 240 170" fill="none" focusable="false">
      <g class="bust-label">
        <path d="m47 67 32-6h96l19 9-8 40-29 7H72l-30-10Z" fill="#562d31" stroke="#ffc481" stroke-width="2" />
        <text x="120" y="100" text-anchor="middle" fill="#ffe4b4" stroke="#7a3530" stroke-width="1" paint-order="stroke" font-size="37" font-weight="900" letter-spacing="5">爆牌</text>
      </g>
    </svg>
  </div>
</template>

<style scoped>
.bust-burst { position: absolute; left: 50%; top: 50%; width: clamp(110px, 16vw, 210px); aspect-ratio: 24 / 17; transform: translate(-50%, -50%); z-index: 9; pointer-events: none; filter: drop-shadow(0 3px 3px #401b2a55); }
.bust-burst svg { position: relative; display: block; width: 100%; height: 100%; overflow: visible; }
.bust-sprite { position: absolute; left: 50%; top: 50%; width: 150%; aspect-ratio: 1; transform: translate(-50%, -50%); background: url('/ui/guochao/burst-strip.webp') 0 0 / 1600% 100% no-repeat; animation: bust-sprite 1.04s steps(15, end) both; }
.bust-label { transform-origin: 120px 85px; }
.bust-label { animation: bust-label 1.1s cubic-bezier(.2,.7,.2,1) both; }
/* 仅在牌旁短暂爆裂；不闪白屏，也不推动手牌和操作控件。 */
@keyframes bust-label { 0% { opacity: 0; transform: scale(.55) rotate(-6deg); } 24% { opacity: 1; transform: scale(1.08) rotate(2deg); } 38%, 76% { opacity: 1; transform: scale(1) rotate(0); } 100% { opacity: 0; transform: scale(.96) translateY(-8px); } }
@keyframes bust-sprite { 0% { background-position: 0 0; opacity: 1; } 94% { opacity: 1; } 100% { background-position: 100% 0; opacity: 0; } }
@media (max-height: 480px) { .bust-burst { width: clamp(105px, 17vw, 145px); } }
@media (prefers-reduced-motion: reduce) { .bust-burst { display: none; } .bust-burst * { animation: none !important; } }
</style>
