<script setup lang="ts">
/* VideoView — 视频设置。阶段2填充。 */
import { useRouter } from 'vue-router';
import { ArrowLeft, Video } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';

const router = useRouter();
function back(): void {
  router.push('/overview');
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle title="视频设置" />
    <BaseEmpty
      :icon="Video"
      title="迁移中"
      description="视频设置视图将在阶段2填充，接入 /api/config/video 表单与保存。"
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
