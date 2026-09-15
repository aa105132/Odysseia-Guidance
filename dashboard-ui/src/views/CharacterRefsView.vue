<script setup lang="ts">
/* CharacterRefsView — 角色参考图库（图生图用角色设定图管理）。
 * 列表 + 弹窗模式：列表卡片显示缩略图/角色名/尺寸/大小，点卡片开大图弹窗看详情与删除；
 * 上传在顶栏"添加角色"按钮，选文件+填角色名后 multipart POST。与月月工具
 * generate_image_with_refs 共用 /app/data/character_refs 目录。 */
import { computed, onMounted, ref } from 'vue';
import {
  FileImage,
  ImagePlus,
  Inbox,
  RefreshCw,
  Trash2,
  X as XIcon,
} from 'lucide-vue-next';
import { useAuthStore } from '@/stores/auth';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseModal from '@/components/ui/BaseModal.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';

interface RefItem {
  name: string;
  filename: string;
  size: number;
  mtime: number;
  dims: { w: number; h: number } | null;
}

const auth = useAuthStore();

const items = ref<RefItem[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const q = ref('');

// 上传弹窗
const showUpload = ref(false);
const uploadName = ref('');
const uploadFile = ref<File | null>(null);
const uploading = ref(false);
const uploadErr = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

// 批量导入（zip）
const zipInput = ref<HTMLInputElement | null>(null);
const zipBusy = ref(false);
const zipResult = ref<{ imported: number; skipped: string[] } | null>(null);

// 大图查看
const viewing = ref<RefItem | null>(null);

// 删除确认
const deleting = ref<RefItem | null>(null);
const deleteBusy = ref(false);

const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase();
  if (!kw) return items.value;
  return items.value.filter((it) => it.name.toLowerCase().includes(kw));
});

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false });
}

function thumbUrl(it: RefItem): string {
  // <img> 标签带不了 Authorization 头，token 走 query
  return `/api/character-refs/${encodeURIComponent(it.name)}/thumb?token=${encodeURIComponent(auth.token)}`;
}

function fullUrl(it: RefItem): string {
  return `/api/character-refs/${encodeURIComponent(it.name)}?token=${encodeURIComponent(auth.token)}`;
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const res = await fetch('/api/character-refs', {
      headers: { Authorization: `Bearer ${auth.token}` },
    });
    if (!res.ok) throw new Error(`加载失败 (${res.status})`);
    const data = await res.json();
    items.value = data.items ?? [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

function pickFile() {
  uploadErr.value = null;
  uploadName.value = '';
  uploadFile.value = null;
  showUpload.value = true;
}

function onFileChange(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0] ?? null;
  uploadFile.value = f;
  // 文件名（去扩展）自动填角色名
  if (f && !uploadName.value.trim()) {
    const stem = f.name.replace(/\.[^.]+$/, '');
    if (stem) uploadName.value = stem;
  }
}

async function onZipChange(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0] ?? null;
  if (!f) return;
  if (zipBusy.value) return;
  zipBusy.value = true;
  zipResult.value = null;
  error.value = null;
  try {
    const fd = new FormData();
    fd.append('file', f);
    const res = await fetch('/api/character-refs/import-zip', {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.token}` },
      body: fd,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ?? `导入失败 (${res.status})`);
    zipResult.value = { imported: data.imported ?? 0, skipped: data.skipped ?? [] };
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : '导入失败';
  } finally {
    zipBusy.value = false;
    if (zipInput.value) zipInput.value.value = '';
  }
}

async function doUpload() {
  if (!uploadFile.value) {
    uploadErr.value = '选一张图片';
    return;
  }
  const name = uploadName.value.trim();
  if (!name) {
    uploadErr.value = '填角色名';
    return;
  }
  if (uploadFile.value.size > 20 * 1024 * 1024) {
    uploadErr.value = '单图上限 20MB';
    return;
  }
  uploading.value = true;
  uploadErr.value = null;
  try {
    const fd = new FormData();
    fd.append('file', uploadFile.value);
    const res = await fetch(`/api/character-refs/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${auth.token}` },
      body: fd,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ?? `上传失败 (${res.status})`);
    showUpload.value = false;
    await load();
  } catch (e) {
    uploadErr.value = e instanceof Error ? e.message : '上传失败';
  } finally {
    uploading.value = false;
  }
}

async function doDelete() {
  if (!deleting.value) return;
  deleteBusy.value = true;
  try {
    const res = await fetch(`/api/character-refs/${encodeURIComponent(deleting.value.name)}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${auth.token}` },
    });
    if (!res.ok) throw new Error(`删除失败 (${res.status})`);
    viewing.value = null;
    deleting.value = null;
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败';
  } finally {
    deleteBusy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="view">
    <BaseSectionTitle title="角色参考图库" description="图生图的角色设定图。月月画「画紫灵和梅凝喝茶」这类多角色图时，会从这里按角色名找参考图。" />

    <div class="toolbar">
      <BaseInput v-model="q" placeholder="搜角色名…" class="search" />
      <div class="toolbar__actions">
        <BaseButton variant="ghost" :disabled="zipBusy" @click="zipInput?.click()">
          <RefreshCw :size="16" :class="{ spin: zipBusy }" />
          {{ zipBusy ? '导入中…' : '导入zip' }}
        </BaseButton>
        <input ref="zipInput" type="file" accept=".zip" class="upload__input" @change="onZipChange" />
        <BaseButton variant="primary" @click="pickFile">
          <ImagePlus :size="16" /> 添加角色
        </BaseButton>
      </div>
    </div>

    <div v-if="zipResult" class="banner banner--ok">
      ✅ 导入 {{ zipResult.imported }} 个角色
      <span v-if="zipResult.skipped.length">，跳过：{{ zipResult.skipped.join('、') }}</span>
      <button class="banner__retry" @click="zipResult = null">知道了</button>
    </div>

    <div v-if="error" class="banner banner--error">
      {{ error }}
      <button class="banner__retry" @click="load">重试</button>
    </div>

    <BaseSkeleton v-if="loading && !items.length" :rows="4" />

    <BaseEmpty v-else-if="!filtered.length && !loading" title="还没有角色参考图">
      <template #icon><Inbox :size="40" /></template>
      <p>点右上"添加角色"上传第一张设定图</p>
    </BaseEmpty>

    <div v-else class="grid">
      <div v-for="it in filtered" :key="it.name" class="card" @click="viewing = it">
        <img :src="thumbUrl(it)" :alt="it.name" class="card__img" loading="lazy" />
        <div class="card__meta">
          <span class="card__name">{{ it.name }}</span>
          <span class="card__sub">
            {{ it.dims ? `${it.dims.w}×${it.dims.h}` : '—' }} · {{ fmtSize(it.size) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 上传弹窗 -->
    <BaseModal v-model="showUpload" title="添加角色参考图">
      <div class="upload">
        <input ref="fileInput" type="file" accept="image/*" class="upload__input" @change="onFileChange" />
        <button class="upload__drop" @click="fileInput?.click()">
          <FileImage :size="28" />
          <span v-if="uploadFile">{{ uploadFile.name }}（{{ fmtSize(uploadFile.size) }}）</span>
          <span v-else>点击选择图片（png/jpg/webp/gif，≤20MB）</span>
        </button>
        <BaseInput v-model="uploadName" placeholder="角色名（如：紫灵）" />
        <p v-if="uploadErr" class="upload__err">{{ uploadErr }}</p>
        <div class="upload__actions">
          <BaseButton variant="ghost" :disabled="uploading" @click="showUpload = false">取消</BaseButton>
          <BaseButton variant="primary" :disabled="uploading" @click="doUpload">
            {{ uploading ? '上传中…' : '入库' }}
          </BaseButton>
        </div>
      </div>
    </BaseModal>

    <!-- 大图查看弹窗 -->
    <BaseModal :model-value="!!viewing" :title="viewing?.name ?? ''" size="lg" @update:model-value="viewing = null">
      <div v-if="viewing" class="viewer">
        <img :src="fullUrl(viewing)" :alt="viewing.name" class="viewer__img" />
        <div class="viewer__meta">
          <span>文件：{{ viewing.filename }}</span>
          <span>尺寸：{{ viewing.dims ? `${viewing.dims.w}×${viewing.dims.h}` : '—' }}</span>
          <span>大小：{{ fmtSize(viewing.size) }}</span>
          <span>更新：{{ fmtTime(viewing.mtime) }}</span>
        </div>
        <div class="viewer__actions">
          <BaseButton variant="ghost" @click="viewing = null"><XIcon :size="16" /> 关闭</BaseButton>
          <BaseButton variant="danger" :disabled="deleteBusy" @click="deleting = viewing">
            <Trash2 :size="16" /> 删除
          </BaseButton>
        </div>
      </div>
    </BaseModal>

    <BaseConfirmDialog
      :model-value="!!deleting"
      title="删除参考图"
      :message="`确定删除「${deleting?.name}」？删除后月月画图就找不到这个角色了。`"
      :variant="'danger'"
      @update:model-value="deleting = null"
      @cancel="deleting = null"
      @confirm="doDelete"
    />
  </div>
</template>

<style scoped>
.view { display: flex; flex-direction: column; gap: 16px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.toolbar .search { max-width: 260px; }
.toolbar__actions { display: flex; gap: 8px; }
.banner { padding: 10px 14px; border-radius: 8px; font-size: 13px; display: flex; gap: 10px; align-items: center; }
.banner--error { background: rgba(220, 60, 60, 0.12); color: #d33; }
.banner--ok { background: rgba(60, 180, 100, 0.12); color: #2a7; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.banner__retry { background: none; border: none; color: #d33; text-decoration: underline; cursor: pointer; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; }
.card { background: var(--surface, #fff); border: 1px solid rgba(0,0,0,0.08); border-radius: 10px; overflow: hidden; cursor: pointer; transition: transform 0.15s, box-shadow 0.15s; }
.card:hover { transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,0.12); }
.card__img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: rgba(0,0,0,0.04); }
.card__meta { padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
.card__name { font-size: 14px; font-weight: 600; }
.card__sub { font-size: 12px; color: #888; }
.upload { display: flex; flex-direction: column; gap: 12px; }
.upload__input { display: none; }
.upload__drop { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 24px; border: 2px dashed rgba(0,0,0,0.15); border-radius: 10px; background: none; cursor: pointer; color: #666; font-size: 13px; }
.upload__drop:hover { border-color: #7c6cf0; color: #7c6cf0; }
.upload__err { color: #d33; font-size: 13px; }
.upload__actions { display: flex; justify-content: flex-end; gap: 8px; }
.viewer { display: flex; flex-direction: column; gap: 12px; }
.viewer__img { max-width: 100%; max-height: 60vh; object-fit: contain; align-self: center; background: rgba(0,0,0,0.03); border-radius: 8px; }
.viewer__meta { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #666; }
.viewer__actions { display: flex; justify-content: space-between; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
