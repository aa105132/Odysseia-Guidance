import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// 阶段0-4：dev server 5173，/api 代理到 8080 后端（与 bot 同进程 integrated_mode）
// 阶段5：vite build 输出到 ../src/dashboard/static 覆盖旧 SPA
// base '/static/'：对齐 FastAPI 的 app.mount("/static")，构建产物引用 /static/assets/...
//   dev 期访问 localhost:5173/static/（base 影响 dev 根路径）
export default defineConfig({
  plugins: [vue()],
  base: '/static/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // 构建产物落入后端静态目录，CI 预构建，.gitignore 忽略，不入库
    outDir: '../src/dashboard/static',
    // 阶段5切换：清空旧产物重新出（旧 SPA 已迁移完成，不再需要 fallback）
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // /api 前缀原样转发到 FastAPI 后端，匹配 @app.get("/api/...") 路由
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
