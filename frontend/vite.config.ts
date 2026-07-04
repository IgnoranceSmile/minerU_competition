import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发期 /api 代理到本地 FastAPI 后端，避免跨域
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
    watch: { usePolling: true, interval: 1000 },
  },
});
