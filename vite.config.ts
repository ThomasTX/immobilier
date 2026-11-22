import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base relative : l'app fonctionne aussi servie depuis un sous-dossier (GitHub Pages…)
export default defineConfig({
  plugins: [react()],
  base: "./",
});
