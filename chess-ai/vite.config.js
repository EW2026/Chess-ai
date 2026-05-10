import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: "./",   // 🔥 THIS is the correct value
  build: {
    outDir: "dist",
    assetsDir: "assets",
  },
})