<script setup lang="ts">
/* EmbeddingView — 向量嵌入。阶段3填充。 */
import { useRouter } from 'vue-router';
import { ArrowLeft, Search } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';

const router = useRouter();
function back(): void {
  router.push('/overview');
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle title="向量嵌入" />
    <BaseEmpty
      :icon="Search"
      title="迁移中"
      description="向量嵌入视图将在阶段3填充，接入嵌入模型与向量库管理。"
      action-text="返回总览"
      :action-icon="ArrowLeft"
      @action="back"
    />
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-8);
}
</style>
