import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  // Relative asset paths. The packaged HUD is served by the backend under the
  // `/hud` subpath (main.py mounts jarvis-frontend/dist there), and the default
  // absolute base would emit `/assets/...` — a 404 against the API root. `./`
  // resolves correctly under any subpath, and under `file://` too if the shell
  // ever needs to fall back to loading off disk.
  base: './',
  plugins: [react(), tailwindcss()],
})
