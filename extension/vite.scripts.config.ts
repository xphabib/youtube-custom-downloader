import { defineConfig } from "vite";

// The background worker and the content script each need a single self-contained
// classic script, so they are built one at a time: --mode background|content.
const ENTRIES = { background: "src/background.ts", content: "src/content/page.ts" } as const;

export default defineConfig(({ mode }) => {
  if (!(mode in ENTRIES)) throw new Error(`Use --mode with one of: ${Object.keys(ENTRIES).join(", ")}`);
  const name = mode as keyof typeof ENTRIES;
  return {
    build: {
      outDir: "dist",
      emptyOutDir: false,
      target: "chrome116",
      sourcemap: false,
      lib: { entry: ENTRIES[name], formats: ["iife"], name: `ytclip_${name}`, fileName: () => `${name}.js` },
    },
  };
});
