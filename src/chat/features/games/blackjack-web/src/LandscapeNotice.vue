<template>
  <aside class="landscape-notice" aria-label="横屏游玩提示" role="status">
    <div class="orientation-phone" aria-hidden="true"><span>↻</span></div>
    <p class="orientation-eyebrow">月月牌室</p>
    <h1>横过来，坐上牌桌</h1>
    <p>旋转手机，完整查看牌桌和操作按钮。</p>
    <small>旋转不会退出房间，牌局计时会继续。</small>
  </aside>
</template>

<style scoped>
.landscape-notice { display: none; }
@media (max-width: 600px) and (orientation: portrait) {
  .landscape-notice { position: fixed; inset: 0; z-index: 10000; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 28px; color: #edf4f7; background: radial-gradient(ellipse at 50% 30%, #213c50, #0b1521 75%); text-align: center; }
  .orientation-phone { width: 94px; height: 52px; border: 2px solid #cbb580; border-radius: 11px; display: grid; place-items: center; margin-bottom: 30px; box-shadow: 0 0 50px #71bce31a; }
  .orientation-phone span { font-size: 34px; color: #e8d4a0; }
  .orientation-eyebrow { color: #baa678; font-size: 12px; letter-spacing: 5px; }
  .landscape-notice h1 { font-size: 25px; margin: 10px 0; font-weight: 600; }
  .landscape-notice p:not(.orientation-eyebrow) { color: #b9c9d5; line-height: 1.7; font-size: 14px; }
  .landscape-notice small { color: #8097a7; margin-top: 20px; font-size: 11px; }
}
</style>
