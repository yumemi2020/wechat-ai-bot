import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    // Docker / NAS 部署時若透過網域存取，請加入你的網域；或改為 allowedHosts: 'all'
    allowedHosts: ['localhost', '127.0.0.1'],
    // allowedHosts: 'all',
  },
})