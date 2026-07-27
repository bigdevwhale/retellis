import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// libsodium-wrappers-sumo@0.7.16 ships a broken ESM build (it imports an
// unpublished ./libsodium-sumo.mjs). Next.js's webpack alias pins every import
// to the CommonJS build (see next.config.ts); mirror that here so the vault
// tests — which drive the real `import('libsodium-wrappers-sumo')` inside
// lib/vault.ts — resolve to the working CJS entry under vitest/esbuild too.
const require = createRequire(import.meta.url);
const sodiumCjs = require.resolve('libsodium-wrappers-sumo');

export default defineConfig({
  esbuild: {
    // Match Next.js / React 19's automatic JSX runtime so `.tsx` fixtures
    // (e.g. icon nodes) transform without a bare `React` import in tests.
    jsx: 'automatic',
  },
  resolve: {
    alias: {
      'libsodium-wrappers-sumo': sodiumCjs,
      // Mirror the ``@/*`` alias from tsconfig so component imports resolve
      // the same way under vitest as they do in the Next build. Without it,
      // tests that import a component (which itself uses ``@/lib/...``)
      // would fail at the import-analysis step before the test runs.
      // The trailing slash + ``find: '\0/'`` trick makes vite match ``@/x``
      // and rewrite it to ``<abs-root>/x`` (without the ``@``).
      '@': fileURLToPath(new URL('./', import.meta.url)),
    },
    extensions: ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.json'],
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
