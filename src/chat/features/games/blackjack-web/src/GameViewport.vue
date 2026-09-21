<template>
  <div class="game-viewport-shell">
    <div class="game-viewport-stage"><slot /></div>
  </div>
</template>

<style scoped>
/* 固定牌桌舞台比例，剩余窗口露出背景；控件使用真实尺寸，不做整体缩放。 */
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
  width: min(100cqw, calc(100cqh * 16 / 9));
  height: min(100cqh, calc(100cqw * 9 / 16));
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  container-type: size;
  container-name: game-viewport;
  box-shadow: 0 0 32px #20163570;
}
</style>
