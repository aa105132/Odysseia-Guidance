<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
const props = defineProps<{ roomId: string }>();
const copied = ref(false);
const manual = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
async function copy() {
  try {
    await navigator.clipboard.writeText(props.roomId);
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => { copied.value = false; }, 2000);
  } catch { manual.value = true; }
}
onBeforeUnmount(() => clearTimeout(timer));
</script>
<template>
  <span class="room-copy"><button type="button" :aria-label="`复制房间号 ${roomId}`" @click="copy"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M8 7V3h13v14h-4M3 7h14v14H3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></svg>{{ copied ? '已复制' : roomId }}</button><input v-if="manual" :value="roomId" readonly aria-label="长按复制房间号" @focus="($event.target as HTMLInputElement).select()" /><span v-if="manual" class="copy-tip">长按复制</span></span>
</template>
<style scoped>
.room-copy { display: inline-flex; align-items: center; gap: 4px; vertical-align: middle; }
.room-copy button { all: unset; box-sizing: border-box; min-height: 36px; display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px; border: 1px solid #f3cf8199; border-radius: 5px; cursor: pointer; color: #fff1bb; background: #473d7366; font-size: 11px; line-height: 1.2; }
.room-copy button:focus-visible { outline: 2px solid #fff1bb; outline-offset: 2px; }
svg { width: 12px; height: 12px; } .room-copy input { width: 70px; min-height: 24px; padding: 2px; } .copy-tip { font-size: 9px; }
</style>
