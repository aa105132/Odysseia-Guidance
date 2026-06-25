<script setup lang="ts">
/* LoginView — 工坊台入口。克制人设锚点：灵石图标 + "月月正在工坊里等你"。
 * 验证流程：client.get('/api/config/all', { token: key }) —— 该端点需 Bearer，
 *   200 = 密钥有效且服务可达；401 = 密钥无效；status=0 = 服务不可达。
 *   （/api/health 免鉴权，只能验连通不能验 token，故用 config/all 验密钥。）
 * 成功后 auth.login(key) + 跳 redirect 或 /overview；失败 toast 错误。 */
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Gem, ArrowRight } from 'lucide-vue-next';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import { client, ApiError } from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const toast = useToastStore();

const secretKey = ref('');
const loading = ref(false);

async function submit(): Promise<void> {
  const key = secretKey.value.trim();
  if (!key) {
    toast.push({ type: 'warning', message: '请输入通行密钥' });
    return;
  }
  loading.value = true;
  try {
    // 显式传 token：auth store 尚未写入，用输入密钥直接命中需鉴权端点验证
    await client.get('/api/config/all', { token: key });
    auth.login(key);
    toast.push({ type: 'success', message: '已进入工坊' });
    const redirect = route.query.redirect;
    router.push(typeof redirect === 'string' ? redirect : '/overview');
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 401) toast.push({ type: 'error', message: '密钥无效' });
      else if (e.status === 0) toast.push({ type: 'error', message: '服务不可达，请确认后端已启动' });
      else toast.push({ type: 'error', message: `登录失败：${e.message}` });
    } else {
      toast.push({ type: 'error', message: '密钥无效或服务不可达' });
    }
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login">
    <div class="login__card">
      <div class="login__brand">
        <Gem class="login__brand-icon" aria-hidden="true" />
        <h1 id="login-title" class="login__title font-display">月月工坊台</h1>
        <p class="login__sub">月月正在工坊里等你，请出示通行密钥。</p>
      </div>

      <form class="login__form" novalidate aria-labelledby="login-title" @submit.prevent="submit">
        <PasswordInput
          v-model="secretKey"
          label="通行密钥"
          placeholder="DASHBOARD_SECRET"
          required
          :disabled="loading"
        />
        <BaseButton
          type="submit"
          variant="primary"
          size="lg"
          :loading="loading"
          :icon="ArrowRight"
          class="login__submit"
        >
          进入工坊
        </BaseButton>
      </form>

      <p class="login__hint">密钥即后端 DASHBOARD_SECRET 环境变量</p>
    </div>
  </div>
</template>

<style scoped>
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-6);
  background: var(--bg-base);
}
.login__card {
  width: 100%;
  max-width: 24rem;
  padding: var(--space-8);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}
.login__brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-2);
  margin-bottom: var(--space-6);
}
.login__brand-icon {
  width: var(--space-8);
  height: var(--space-8);
  color: var(--accent);
}
.login__title {
  font-size: var(--text-2xl);
  color: var(--text-primary);
}
.login__sub {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}
.login__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.login__submit {
  width: 100%;
}
.login__hint {
  margin-top: var(--space-4);
  text-align: center;
  font-size: var(--text-xs);
  color: var(--text-muted);
}
</style>
