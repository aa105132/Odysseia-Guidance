/* client.ts — 后端 API 封装
 * 统一注入 Authorization: Bearer <token>（取自 auth store，或调用处显式传 token 用于登录验证）。
 * 非 2xx 抛 ApiError，承载 status 与后端 detail；网络错抛 status=0 的 ApiError。
 * 代理：dev 期 /api 由 vite proxy 转发到 8080，与 bot 同进程 integrated_mode。 */
import { useAuthStore } from '@/stores/auth';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

interface RequestOptions {
  method?: string;
  body?: string;
  headers?: Record<string, string>;
  /** 显式 token，绕过 auth store（用于登录态尚未写入前的验证请求） */
  token?: string;
  signal?: AbortSignal;
}

type BaseOpts = Omit<RequestOptions, 'method' | 'body'>;

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const auth = useAuthStore();
  const token = opts.token ?? auth.token;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers ?? {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body,
      signal: opts.signal,
    });
  } catch {
    throw new ApiError(0, '服务不可达，请检查后端是否运行');
  }

  if (!res.ok) {
    let message = res.statusText || `请求失败 (${res.status})`;
    try {
      const body = await res.json();
      if (body && body.detail) {
        message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* 非 JSON 响应，沿用 statusText */
    }
    throw new ApiError(res.status, message);
  }

  // 204 等无内容响应
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const client = {
  get: <T>(path: string, opts?: BaseOpts): Promise<T> =>
    request<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: BaseOpts): Promise<T> =>
    request<T>(path, {
      ...opts,
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body?: unknown, opts?: BaseOpts): Promise<T> =>
    request<T>(path, {
      ...opts,
      method: 'PUT',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  delete: <T>(path: string, opts?: BaseOpts): Promise<T> =>
    request<T>(path, { ...opts, method: 'DELETE' }),
};
