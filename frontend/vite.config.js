import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  base: '/app/',
  server: {
    proxy: {
      '/city': 'http://localhost:8000',
      '/simulate': 'http://localhost:8000',
      '/criticality': 'http://localhost:8000',
      '/compare': 'http://localhost:8000',
    },
  },
})
