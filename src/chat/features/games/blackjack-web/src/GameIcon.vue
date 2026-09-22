<script setup lang="ts">
type IconName = 'blackjack' | 'landlord' | 'mahjong' | 'texas' | 'golden_flower' | 'beginner' | 'intermediate' | 'advanced' | 'solo' | 'friends' | 'room' | 'robot' | 'leaderboard';
const props = defineProps<{ name: IconName }>();
const paintedGames = ['blackjack', 'landlord', 'mahjong', 'texas', 'golden_flower'];
</script>

<template>
  <img v-if="paintedGames.includes(props.name)" class="game-icon painted-game-icon" :src="`/ui/guochao/game-${name}.webp`" alt="" aria-hidden="true" draggable="false">
  <svg v-else class="game-icon" viewBox="0 0 160 120" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
    <ellipse cx="80" cy="104" rx="55" ry="8" fill="#041819" opacity=".24" />
    <g v-if="name === 'leaderboard'">
      <defs><linearGradient id="rank-gold" x1="40" y1="20" x2="113" y2="99" gradientUnits="userSpaceOnUse"><stop stop-color="#fff2ad"/><stop offset=".48" stop-color="#eab951"/><stop offset="1" stop-color="#a96428"/></linearGradient></defs>
      <circle cx="80" cy="53" r="46" fill="#fff3b4" opacity=".15"/>
      <path d="M53 30H32v16c0 17 15 25 29 25m46-41h21v16c0 17-15 25-29 25" stroke="#a46430" stroke-width="12" stroke-linejoin="round"/>
      <path d="M53 28H32v16c0 17 15 25 29 25m46-41h21v16c0 17-15 25-29 25" stroke="#f5d382" stroke-width="7" stroke-linejoin="round"/>
      <path d="M49 19h62v24c0 23-13 36-31 36S49 66 49 43Z" fill="url(#rank-gold)" stroke="#fff0b6" stroke-width="3"/>
      <path d="m80 30 6 12 13 2-10 9 2 13-11-6-11 6 2-13-10-9 13-2Z" fill="#fff5c4" stroke="#b27c36" stroke-width="2"/>
      <path d="M74 78h12v15h-12Z" fill="#deb05e"/><path d="M61 92h38l8 12H53Z" fill="url(#rank-gold)" stroke="#ffe4a0" stroke-width="2"/>
      <path d="m27 77 6 11 12-3m88-8-6 11-12-3" stroke="#ecd386" stroke-width="3" stroke-linecap="round"/>
    </g>
    <g v-else-if="name === 'beginner' || name === 'intermediate' || name === 'advanced'">
      <circle cx="80" cy="57" r="46" :fill="name === 'beginner' ? '#7ECBC1' : name === 'intermediate' ? '#94B8EA' : '#E7BF70'" opacity=".14" />
      <path d="m80 11 38 20v37L80 98 42 68V31z" :fill="name === 'beginner' ? '#276D63' : name === 'intermediate' ? '#315582' : '#83612D'" :stroke="name === 'beginner' ? '#91DACA' : name === 'intermediate' ? '#ADC9EF' : '#F3D399'" stroke-width="3" />
      <path d="m80 21 29 15v27L80 86 51 63V36z" stroke="#FFFFFF" opacity=".18" stroke-width="2" />
      <path v-if="name === 'beginner'" d="M81 73V44m0 20c-17 0-23-10-21-20 15 0 22 8 21 20Zm0-8c0-16 11-23 23-21 0 15-10 23-23 21Z" fill="#D0F0BC" stroke="#A4D5A6" stroke-width="2" />
      <g v-else-if="name === 'intermediate'" fill="#DBE8FC"><path d="m70 35 5 11 12 1-9 9 2 12-10-6-11 6 2-12-9-9 12-1z" /><path d="m96 58 3 6 7 1-5 5 1 7-6-4-6 4 1-7-5-5 7-1z" /></g>
      <path v-else d="m56 42 14 8 10-17 12 17 14-8-7 29H63z" fill="#FFE3A0" stroke="#FFEFBD" stroke-width="2" />
      <path d="M47 83 29 94l20 2 7 13 13-18m44-8 18 11-20 2-7 13-13-18" :fill="name === 'advanced' ? '#BD914F' : '#5B8590'" stroke="#102B38" stroke-width="2" />
      <path d="m58 97 22-12 22 12" :stroke="name === 'advanced' ? '#F5D697' : '#AFD6DA'" stroke-width="3" />
    </g>
    <g v-else-if="name === 'solo'">
      <circle cx="80" cy="55" r="45" fill="#D9B578" opacity=".14" />
      <path d="M45 102c0-27 14-39 34-39s36 12 36 39" fill="#347E7B" stroke="#164C51" stroke-width="3" />
      <circle cx="80" cy="41" r="24" fill="#E4C8A4" stroke="#AF845D" stroke-width="2" />
      <path d="M57 41c-4-27 40-41 49-12-10 2-19-3-22-8-5 13-15 17-27 20Z" fill="#315469" /><path d="M71 68 80 84l11-16" fill="#F4E6C8" />
      <rect x="96" y="66" width="33" height="43" rx="5" fill="#FFF3D4" stroke="#AE8F55" stroke-width="2" transform="rotate(12 112 87)" />
      <path d="m111 75-9 14h6l-3 11 16-18h-8l3-7z" fill="#BD9250" />
    </g>
    <g v-else-if="name === 'friends'">
      <circle cx="80" cy="55" r="46" fill="#79BED3" opacity=".14" />
      <path d="M16 100c0-22 11-35 29-35s28 13 28 35" fill="#487E93" stroke="#234D61" stroke-width="2" /><circle cx="45" cy="47" r="19" fill="#E5C6A2" /><path d="M26 45c-3-24 37-31 39-3-13-2-19-7-20-11-2 7-8 12-19 14Z" fill="#2E4B60" />
      <path d="M87 100c0-22 11-35 29-35s28 13 28 35" fill="#AC795D" stroke="#694C45" stroke-width="2" /><circle cx="116" cy="47" r="19" fill="#E5C6A2" /><path d="M97 43c-1-23 36-24 39 3l-15-7-8-8c-2 7-9 12-16 12Z" fill="#694E43" />
      <path d="M48 104c0-28 12-45 32-45s33 17 33 45" fill="#41938C" stroke="#215C62" stroke-width="3" /><circle cx="80" cy="39" r="22" fill="#F1D5AB" /><path d="M58 39c-3-29 40-34 44-2-11 0-24-5-29-14-1 7-6 12-15 16Z" fill="#304C59" /><path d="M72 64 80 77l8-13" fill="#F2EBD5" />
    </g>
    <g v-else-if="name === 'room'">
      <circle cx="80" cy="57" r="45" fill="#89BACA" opacity=".14" />
      <path d="m27 56 53-37 53 37" stroke="#E5C38A" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" /><path d="M38 55h84v49H38z" fill="#2A6572" stroke="#92BAC2" stroke-width="3" /><path d="M65 69h30v35H65z" fill="#112F43" stroke="#7899A0" stroke-width="2" /><circle cx="87" cy="88" r="3" fill="#E8C177" /><path d="M49 67h8v13h-8m45-13h8v13h-8" fill="#E8C88C" />
    </g>
    <g v-else>
      <circle cx="80" cy="56" r="45" fill="#81C6C0" opacity=".14" /><path d="M80 24v-9" stroke="#D9B778" stroke-width="4" /><circle cx="80" cy="12" r="5" fill="#EDD09A" /><rect x="39" y="29" width="82" height="63" rx="16" fill="#739FAD" stroke="#C2DDDE" stroke-width="3" /><rect x="49" y="40" width="62" height="34" rx="10" fill="#173B4D" /><circle cx="64" cy="56" r="7" fill="#D9EAC8" /><circle cx="96" cy="56" r="7" fill="#D9EAC8" /><path d="M69 82h22" stroke="#214D5C" stroke-width="4" stroke-linecap="round" /><path d="M31 47v25m98-25v25M56 97v10m48-10v10" stroke="#A7C6CB" stroke-width="8" stroke-linecap="round" />
    </g>
  </svg>
</template>

<style scoped>
.game-icon { display: block; width: 100%; height: 100%; overflow: visible; object-fit: contain; }
</style>
