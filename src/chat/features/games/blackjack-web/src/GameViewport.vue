<template>
  <div class="game-viewport-shell">
    <div class="game-viewport-stage"><slot /></div>
  </div>
</template>

<style scoped>
/* 牌桌占满活动可用区域；控件按容器尺寸适配，保留真实触控尺寸。 */
.game-viewport-shell {
  position: relative;
  display: grid;
  place-items: center;
  flex: 1 1 0;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  container-type: size;
  background: linear-gradient(#2f23474f, #241c4a9c), url('/ui/guochao/teahouse-room.webp') center / cover;
}
.game-viewport-stage {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  container-type: size;
  container-name: game-viewport;
}
</style>
