<script setup lang="ts">
withDefaults(defineProps<{
  title: string;
  subtitle?: string;
  detail?: string;
  tone?: 'win' | 'loss' | 'push' | 'mahjong';
  animated?: boolean;
}>(), { subtitle: '', detail: '', tone: 'win', animated: false });
</script>

<template>
  <section class="round-feedback" :data-tone="tone" :data-animated="animated" role="status" aria-live="polite" aria-atomic="true">
    <svg class="result-frame" viewBox="0 0 400 140" preserveAspectRatio="none" fill="none" aria-hidden="true" focusable="false">
      <path class="result-ribbon" d="M37 43 4 34l12 30L4 91l38-6M363 43l33-9-12 30 12 27-38-6" />
      <path class="result-plate" d="M52 13h121l27-9 27 9h121l29 27v64l-29 23H52l-29-23V40Z" />
      <path class="result-border" d="M55 19h118l27-8 27 8h118l25 24v57l-25 21H55l-25-21V43Z" />
      <path class="result-etch" d="m42 48 16-16h40m204 0h40l16 16M42 94l16 16h40m204 0h40l16-16M183 127l17 6 17-6" />
      <path class="result-glint" d="m199 3 3 6 6 2-6 2-3 6-2-6-6-2 6-2z" />
    </svg>
    <svg v-if="animated" class="result-sparks" viewBox="0 0 400 140" fill="currentColor" aria-hidden="true" focusable="false">
      <path class="spark spark-a" d="m50 22 3 7 7 3-7 3-3 7-3-7-7-3 7-3z" />
      <path class="spark spark-b" d="m350 22 3 7 7 3-7 3-3 7-3-7-7-3 7-3z" />
      <path class="spark spark-c" d="m25 88 2 5 5 2-5 2-2 5-2-5-5-2 5-2z" />
      <path class="spark spark-d" d="m375 88 2 5 5 2-5 2-2 5-2-5-5-2 5-2z" />
      <circle class="spark spark-e" cx="107" cy="11" r="3" />
      <circle class="spark spark-f" cx="293" cy="11" r="3" />
      <path class="spark spark-g" d="m100 126 3-5 3 5-3 5z" />
      <path class="spark spark-h" d="m300 126 3-5 3 5-3 5z" />
    </svg>
    <div class="result-copy">
      <div class="result-heading">
        <svg class="result-emblem" viewBox="0 0 40 40" fill="none" aria-hidden="true" focusable="false">
          <g v-if="tone === 'win'" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
            <path d="M11 6h18v12c0 7-5 11-9 11s-9-4-9-11Z" fill="currentColor" fill-opacity=".16" />
            <path d="M11 10H5v6c0 5 4 8 9 8m15-14h6v6c0 5-4 8-9 8M20 29v6m-8 0h16" />
            <path d="m20 10 2 5 5 1-4 3 1 5-4-3-4 3 1-5-4-3 5-1Z" fill="currentColor" stroke="none" />
          </g>
          <g v-else-if="tone === 'mahjong'" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
            <rect x="8" y="3" width="25" height="34" rx="4" fill="currentColor" fill-opacity=".14" />
            <path d="M11 34h18M20 10v18m-7-13h14v9H13Z" />
          </g>
          <g v-else-if="tone === 'loss'" stroke="currentColor" stroke-width="2" stroke-linejoin="round">
            <path d="m20 4 13 5v12c0 7-6 12-13 16C13 33 7 28 7 21V9Z" fill="currentColor" fill-opacity=".12" />
            <path d="m24 9-7 10 7 2-7 11M4 31l3 4m26 0 3-4" />
          </g>
          <g v-else stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
            <circle cx="20" cy="20" r="16" fill="currentColor" fill-opacity=".12" />
            <path d="M12 16h16m-16 8h16" />
          </g>
        </svg>
        <h2 class="round-feedback-title">{{ title }}</h2>
      </div>
      <p v-if="subtitle" class="round-feedback-subtitle">{{ subtitle }}</p>
      <p v-if="detail" class="round-feedback-detail">{{ detail }}</p>
    </div>
  </section>
</template>

<style scoped>
.round-feedback {
  --result-accent: #ffdc91;
  --result-ink: #fff1b9;
  --result-base: #77508b;
  --result-edge: #c2944a;
  position: relative;
  isolation: isolate;
  width: 100%;
  max-width: 360px;
  padding: var(--result-padding, 18px 25px);
  box-sizing: border-box;
  color: var(--result-accent);
  text-align: center;
  pointer-events: none;
  filter: drop-shadow(0 6px 8px #00151d80);
}
.round-feedback[data-tone="loss"] { --result-accent: #edc3a6; --result-ink: #fff0db; --result-base: #8f5363; --result-edge: #bb9569; }
.round-feedback[data-tone="push"] { --result-accent: #f0d59f; --result-ink: #fff1d2; --result-base: #486da1; --result-edge: #b89357; }
.round-feedback[data-tone="mahjong"] { --result-accent: #ffe295; --result-ink: #fff1bb; --result-base: #b96548; --result-edge: #d3a34c; }
.result-frame, .result-sparks { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; z-index: -1; }
.result-ribbon { fill: var(--result-edge); stroke: var(--result-accent); stroke-width: 1; }
.result-plate { fill: var(--result-base); stroke: var(--result-edge); stroke-width: 2; }
.result-border { stroke: var(--result-accent); stroke-width: 1; opacity: .8; }
.result-etch { stroke: var(--result-accent); opacity: .45; }
.result-glint { fill: var(--result-accent); }
.result-copy { position: relative; display: grid; justify-items: center; gap: 5px; min-width: 0; }
.result-heading { display: flex; justify-content: center; align-items: center; gap: 8px; min-width: 0; width: 100%; }
.result-emblem { flex: 0 0 30px; width: 30px; height: 30px; }
.round-feedback-title { margin: 0; color: var(--result-ink); font-size: var(--result-title-size, clamp(22px, 2.6vw, 32px)); font-weight: 900; letter-spacing: 2px; line-height: 1.15; overflow-wrap: anywhere; text-shadow: 0 2px 0 #624167, 1px 0 0 #795435, -1px 0 0 #795435; }
.round-feedback-subtitle, .round-feedback-detail { margin: 0; max-width: 100%; overflow-wrap: anywhere; line-height: 1.35; }
.round-feedback-subtitle { color: #f4eee2; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.round-feedback-detail { color: var(--result-accent); font-size: 11px; }
/* 动画只在新结局挂载时触发，静态恢复不闪屏，结束后保留可读的结果。 */
.round-feedback[data-animated="true"] .result-frame { animation: result-unfold .56s cubic-bezier(.16,.8,.28,1) both; }
.round-feedback[data-animated="true"] .result-copy { animation: result-copy-in .48s .1s ease-out both; }
.spark { opacity: 0; transform-box: fill-box; transform-origin: center; animation: result-spark 1.25s .2s ease-out both; }
.spark-a, .spark-c { --spark-x: -16px; --spark-y: -9px; }
.spark-b, .spark-d { --spark-x: 16px; --spark-y: -9px; }
.spark-e { --spark-x: -7px; --spark-y: -17px; animation-delay: .3s; }
.spark-f { --spark-x: 7px; --spark-y: -17px; animation-delay: .3s; }
.spark-g { --spark-x: -10px; --spark-y: 10px; animation-delay: .35s; }
.spark-h { --spark-x: 10px; --spark-y: 10px; animation-delay: .35s; }
.round-feedback[data-tone="loss"] .result-sparks { display: none; }
@keyframes result-unfold { from { opacity: 0; transform: scaleX(.68) scaleY(.9); } 70% { opacity: 1; transform: scaleX(1.025) scaleY(1.015); } to { opacity: 1; transform: scale(1); } }
@keyframes result-copy-in { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: translateY(0); } }
@keyframes result-spark { 0% { opacity: 0; transform: scale(.3); } 25% { opacity: .9; } 100% { opacity: 0; transform: translate(var(--spark-x), var(--spark-y)) scale(.5); } }
@media (max-height: 480px) {
  .round-feedback { padding: var(--result-padding, 11px 19px); }
  .result-copy { gap: 2px; }
  .result-heading { gap: 5px; }
  .result-emblem { flex-basis: 22px; width: 22px; height: 22px; }
  .round-feedback-title { font-size: var(--result-title-size, 22px); letter-spacing: 1px; }
  .round-feedback-subtitle { font-size: 11px; }
  .round-feedback-detail { font-size: 10px; }
}
@media (prefers-reduced-motion: reduce) {
  .round-feedback *, .round-feedback[data-animated="true"] * { animation: none !important; }
  .result-sparks { display: none; }
}
</style>
