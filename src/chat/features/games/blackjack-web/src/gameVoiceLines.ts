import dialogue from './dialogue.json';
import { yueyueVoiceLines } from './yueyueVoiceLines';

export const quickVoiceLines = [
  { id: 'hurry', text: '快点吧，我等到花儿都谢了' },
  { id: 'nice', text: '你的牌打得也太好了' },
  { id: 'hello', text: '很高兴和你一起玩' },
  { id: 'mm_or_gg', text: '你是MM还是GG？' },
  { id: 'thanks', text: '谢谢你' },
  { id: 'well_played', text: '这局打得漂亮' },
  { id: 'partner', text: '队友，我们配合一下' },
  { id: 'let_me', text: '这轮让我来' },
  { id: 'thinking', text: '别急，我想想怎么打' },
  { id: 'good_luck', text: '祝大家好运' },
  { id: 'again', text: '再来一局吧' },
] as const;
export const welcomeVoiceLines = dialogue.welcome.map((text, index) => ({ id: `welcome_${index}`, text }));
export const welcomeVoiceByText: Record<string, string> = Object.fromEntries(welcomeVoiceLines.map(line => [line.text, line.id]));
export const actionVoiceIds = ['raise', 'call', 'check', 'fold', 'all_in', 'look', 'compare', 'bid', 'no_bid', 'play', 'pass', 'bomb', 'rocket', 'discard', 'pung', 'kong', 'win', 'hit', 'stand'] as const;
export const gameVoiceIds = new Set<string>([...quickVoiceLines.map(line => line.id), ...welcomeVoiceLines.map(line => line.id), ...actionVoiceIds, ...yueyueVoiceLines.map(line => line.id)]);
