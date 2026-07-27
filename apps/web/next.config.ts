import { createRequire } from 'node:module';
import type { NextConfig } from 'next';

// libsodium-wrappers-sumo@0.7.16 ships a broken ESM build (it imports an
// unpublished ./libsodium-sumo.mjs). The CommonJS build is fine, but the
// package's `exports` field forces the ESM entry on `import()`. Resolve the CJS
// entry's absolute path via createRequire (portable: resolves correctly on both
// the dev host and the Docker build) and alias every import to it.
const require = createRequire(import.meta.url);
const sodiumCjs = require.resolve('libsodium-wrappers-sumo');

const config: NextConfig = {
  reactStrictMode: true,
  // Emit a self-contained standalone server (`.next/standalone`) so the runtime
  // image needs no pnpm and no node_modules copy — just `node server.js`. This
  // is the canonical Next.js Docker production layout and avoids shipping a
  // package manager into the runner stage.
  output: 'standalone',
  // The service worker is registered client-side from /sw.js (see components/SwRegister.tsx).
  // Serwist/next-pwa integration is intentionally deferred to keep `next build` robust;
  // installability is preserved via public/manifest.webmanifest + icons.
  async headers() {
    return [
      {
        source: '/sw.js',
        // Service-Worker-Allowed lets the SW scope the whole origin; no-cache
        // guarantees the browser's update check always fetches the freshest
        // script byte-for-byte, so a fixed SW reaches clients on the next
        // reload instead of serving a stale cached sw.js.
        headers: [
          { key: 'Service-Worker-Allowed', value: '/' },
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
        ],
      },
    ];
  },
  // Server-side proxy for /v1 so the browser sees ONE origin (the session
  // cookie is SameSite=Lax first-party). The rewrite target is resolved at
  // standalone-server startup from process.env, so it works in every mode:
  //   - pnpm dev: defaults to http://localhost:8000 (the local API).
  //   - docker web container: API_PROXY_TARGET=http://api:8000 lets :3000 work
  //     standalone (browser → web, web proxies /v1 to the API on the docker
  //     network — same-origin, no CORS, cookie rides along).
  //   - production behind Caddy: leave API_PROXY_TARGET unset → no rewrite;
  //     Caddy routes /v1 directly to the API and never hits Next.
  async rewrites() {
    const target = (
      process.env.API_PROXY_TARGET ??
      (process.env.NODE_ENV === 'production'
        ? ''
        : (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'))
    ).replace(/\/$/, '');
    if (!target) return [];
    return [{ source: '/v1/:path*', destination: `${target}/v1/:path*` }];
  },
  webpack: (cfg) => {
    cfg.resolve = cfg.resolve ?? {};
    cfg.resolve.alias = {
      ...cfg.resolve.alias,
      'libsodium-wrappers-sumo$': sodiumCjs,
    };
    return cfg;
  },
};

export default config;
