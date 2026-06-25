/* voice.ts — 语音合成配置领域 API
 * 对应 GET/PUT /api/config/voice、POST /api/config/test-voice。
 * test-voice 返回二进制音频 blob（非 JSON），client.ts 默认 res.json() 无法处理，
 * 故 testVoice 绕过 client，自写 fetch 取 blob + objectURL，并读 X-Voice-* 响应头。 */
import { client, ApiError } from '../client';
import { useAuthStore } from '@/stores/auth';
import type { VoiceConfig } from '../models';

/** GET /api/config/voice — 读取语音配置（provider: doubao/siliconflow/custom/xiaomi） */
export function getVoiceConfig(): Promise<VoiceConfig> {
  return client.get<VoiceConfig>('/api/config/voice');
}

/** PUT /api/config/voice — 更新语音配置。
 * 后端 PUT 仅回 `{success, updated}`（updated 含 service_available/service_reload_error），
 * 不返回完整配置；故保存后重新 GET 刷新 masked 字段，供 useConfigForm 回填 form/original。 */
export async function saveVoiceConfig(body: Partial<VoiceConfig>): Promise<VoiceConfig> {
  await client.put('/api/config/voice', body);
  return getVoiceConfig();
}

/** test-voice 请求体（对应后端 VoiceTestRequest，全可选除 text） */
export interface VoiceTestRequest {
  text: string;
  voice_type?: string | null;
  emotion?: string | null;
  enable_emotion?: boolean | null;
  emotion_scale?: number | null;
}

/** test-voice 结果：objectURL 供 <audio> 播放 + 元信息（取自 X-Voice-* 响应头） */
export interface VoiceTestResult {
  url: string; // blob objectURL，调用方负责 revokeObjectURL 释放
  mime: string;
  provider: string;
  model: string;
  voiceType: string;
  ext: string;
  size: number; // 字节
}

/** POST /api/config/test-voice — 试听，返回二进制音频。
 * 绕过 client.post（其永远 res.json()），自写 fetch：
 * - 注入 Authorization: Bearer <auth.token>（与 client.ts 同源）
 * - !res.ok 时读 text 一次，先试 JSON.parse 取 detail/error，失败回退原文 slice 200 字
 * - 成功 res.blob() → URL.createObjectURL，从响应头读 X-Voice-Provider/Model/Type/Ext
 * - blob.size<=0 视为失败（后端未返回音频数据） */
export async function testVoice(body: VoiceTestRequest): Promise<VoiceTestResult> {
  const auth = useAuthStore();
  let res: Response;
  try {
    res = await fetch('/api/config/test-voice', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, '服务不可达，请检查后端是否运行');
  }

  if (!res.ok) {
    let message = res.statusText || `请求失败 (${res.status})`;
    try {
      // body 只能消费一次：先取 text，再尝试 JSON.parse
      const txt = await res.text();
      if (txt) {
        try {
          const data = JSON.parse(txt);
          if (data && (data.detail ?? data.error ?? data.message)) {
            message = String(data.detail ?? data.error ?? data.message);
          }
        } catch {
          message = txt.slice(0, 200);
        }
      }
    } catch {
      /* 保持 statusText */
    }
    throw new ApiError(res.status, message);
  }

  const blob = await res.blob();
  if (!blob || blob.size <= 0) {
    throw new ApiError(500, '未返回音频数据');
  }
  const url = URL.createObjectURL(blob);
  return {
    url,
    mime: blob.type || res.headers.get('Content-Type') || 'application/octet-stream',
    provider: res.headers.get('X-Voice-Provider') ?? '',
    model: res.headers.get('X-Voice-Model') ?? '',
    voiceType: res.headers.get('X-Voice-Type') ?? '',
    ext: res.headers.get('X-Voice-Ext') ?? '',
    size: blob.size,
  };
}
