const fs = require('fs');
const file = 'src/chat/features/games/blackjack-web/src/App.vue';
let content = fs.readFileSync(file, 'utf8');

// 1. section class
content = content.replace(
  `<section v-else-if="viewMode === 'single'" id="game-view" class="table-wrapper">`,
  `<section v-else-if="viewMode === 'single'" id="game-view" class="table-wrapper single-mode-view">`
);

// 2. padding
content = content.replace(
  `padding: 2vh 0 10vh 0;"`,
  `padding: 4vh 0 6vh 0;"`
);

// 3. h2 font sizes (replace all in the single section)
// We can use regex with lookahead or just replace specific ones.
content = content.replace(
  /<h2 style="font-family: 'Playfair Display', serif; font-size: 1\.5em; color: #f0e6d2; margin-bottom: 5px; opacity: 0\.9; border: none; min-width: auto; padding-bottom: 0;">月月/g,
  `<h2 style="font-family: 'Playfair Display', serif; font-size: 1.8em; color: #f0e6d2; margin-bottom: 10px; opacity: 0.9; border: none; min-width: auto; padding-bottom: 0;">月月`
);
content = content.replace(
  /<h2 style="font-family: 'Playfair Display', serif; font-size: 1\.5em; color: #f0e6d2; margin-bottom: 5px; opacity: 0\.9; border: none; min-width: auto; padding-bottom: 0;">玩家/g,
  `<h2 style="font-family: 'Playfair Display', serif; font-size: 1.8em; color: #f0e6d2; margin-bottom: 10px; opacity: 0.9; border: none; min-width: auto; padding-bottom: 0;">玩家`
);

// 4. class="card" to class="card large-card" in single mode
// The single mode hand has: <img v-for="(card, index) in singleGame?.dealer_hand || []" :key="'dealer-' + index + '-' + card" :src="cardImageSrc(card)" class="card">
content = content.replace(
  `:src="cardImageSrc(card)" class="card"`,
  `:src="cardImageSrc(card)" class="card large-card"`
);
content = content.replace(
  `:src="cardImageSrc(card)" class="card"`,
  `:src="cardImageSrc(card)" class="card large-card"`
);

// 5. Messages text
content = content.replace(
  `style="margin: 5px 0; font-weight: bold; font-size: 1.2em; color: #c0a062;"`,
  `style="margin: 15px 0; font-weight: bold; font-size: 1.5em; color: #c0a062;"`
);
content = content.replace(
  `style="height: 24px; margin: 5px 0;"`,
  `style="height: 36px; margin: 15px 0;"`
);

// 6. Controls Margin
content = content.replace(
  `margin-top: 20px; display: flex; flex-direction: column; align-items: center; width: 100%; z-index: 20;"`,
  `margin-top: 30px; display: flex; flex-direction: column; align-items: center; width: 100%; z-index: 20;"`
);

// 7. Buttons
content = content.replace(
  `<div id="controls" v-if="canSingleOperate" style="display: flex; gap: 10px;">
                        <button @click="singleHit" :disabled="requestInFlight">要牌</button>
                        <button @click="singleStand" :disabled="requestInFlight">停牌</button>
                        <button @click="singleDouble" :disabled="requestInFlight || !canSingleDouble">双倍下注</button>`,
  `<div id="controls" v-if="canSingleOperate" style="display: flex; gap: 15px;">
                        <button class="single-btn" @click="singleHit" :disabled="requestInFlight">要牌</button>
                        <button class="single-btn" @click="singleStand" :disabled="requestInFlight">停牌</button>
                        <button class="single-btn" @click="singleDouble" :disabled="requestInFlight || !canSingleDouble">双倍下注</button>`
);

// 8. Balance text
content = content.replace(
  `class="balance-text" style="background-color: rgba(0, 0, 0, 0.6); padding: 5px 15px; border-radius: 4px; margin-bottom: 10px; display: inline-block;"`,
  `class="balance-text" style="background-color: rgba(0, 0, 0, 0.6); padding: 8px 20px; border-radius: 6px; margin-bottom: 15px; display: inline-block; font-size: 1.2em;"`
);

// 9. Input and replay
content = content.replace(
  `<input v-model.number="singleBetInput" type="number" min="1" placeholder="输入赌注" :disabled="requestInFlight" style="width: 120px;">
                                      <button :disabled="requestInFlight || !canSingleStart" @click="startSingleGame">再来一局</button>`,
  `<input v-model.number="singleBetInput" type="number" min="1" placeholder="输入赌注" :disabled="requestInFlight" style="width: 160px; font-size: 1.2em; padding: 10px;">
                                      <button class="single-btn" :disabled="requestInFlight || !canSingleStart" @click="startSingleGame">再来一局</button>`
);

// 10. Styles
const styles = `
.single-mode-view .large-card {
  width: 200px;
  height: 290px;
  font-size: 2.5em;
  margin: 5px;
}

.single-mode-view .hand .large-card:not(:first-child) {
  margin-left: -100px;
}

.single-mode-view .single-btn {
  padding: 12px 30px;
  font-size: 1.2em;
}

@media (max-width: 1024px) {
  .single-mode-view .large-card {
    width: 160px;
    height: 232px;
  }
  .single-mode-view .hand .large-card:not(:first-child) {
    margin-left: -80px;
  }
}

@media (max-width: 768px) {
  .single-mode-view .large-card {
    width: 120px;
    height: 175px;
  }
  .single-mode-view .hand .large-card:not(:first-child) {
    margin-left: -60px;
  }
  .single-mode-view .single-btn {
    padding: 10px 20px;
    font-size: 1.1em;
  }
}

@media (max-width: 480px) {
  .single-mode-view .large-card {
    width: 90px;
    height: 130px;
  }
  .single-mode-view .hand .large-card:not(:first-child) {
    margin-left: -45px;
  }
}

:global(html),
`;

content = content.replace(
  `<style scoped>
:global(html),`,
  `<style scoped>` + styles
);

fs.writeFileSync(file, content);
console.log('done');
