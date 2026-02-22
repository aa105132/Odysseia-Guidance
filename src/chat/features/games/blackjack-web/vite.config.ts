import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig(() => {
    // 长期方案：每次构建默认注入新的资源版本号，用于 Discord/WebView 强制刷新静态资源缓存
    const assetVersion = (process.env.VITE_ASSET_VERSION ?? '').trim() || `${Date.now()}`;
    const apiProxyTarget =
        (process.env.VITE_API_PROXY_TARGET ?? '').trim() || 'http://127.0.0.1:8484';

    return {
        base: '/',
        publicDir: 'public',
        plugins: [vue()],
        // 设置环境变量目录为项目的根目录 (修正路径)
        envDir: '../../../../../',
        define: {
            // 若外部未传 VITE_ASSET_VERSION，则自动回退到构建时间戳
            'import.meta.env.VITE_ASSET_VERSION': JSON.stringify(assetVersion),
        },
        server: {
            host: true, // 允许来自任何地址的连接
            hmr: false, // 禁用HMR以解决Discord CSP问题
            allowedHosts: ['bring-optional-models-interviews.trycloudflare.com', '.trycloudflare.com'], // 允许Cloudflare隧道主机
            proxy: {
                // 将所有/api开头的请求代理到Python后端
                '/api': {
                    target: apiProxyTarget, // 本地默认走 8484，可通过 VITE_API_PROXY_TARGET 覆盖
                    changeOrigin: true,
                    // The rewrite rule has been removed to ensure the /api prefix is forwarded to the backend,
                    // matching the FastAPI router definition (e.g., @app.get("/api/user")).
                },
            },
        },
    };
});