#!/usr/bin/env node
// Drift check: compare the pydantic JSON-Schema (schema.json, produced by
// gen_json_schema.py) against the zod schemas (src/ts/index.ts) converted via
// zod-to-json-schema. Exits 1 if the per-model property-key sets diverge.
//
// If the TS-side deps (zod / zod-to-json-schema) or type-stripping are
// unavailable, the check warns and exits 0 so it never blocks `docker compose
// up`; run it in a dev/CI shell where the contracts package is installed.
//
// I22: pass `--strict` (or set CONTRACTS_STRICT=1) to make the missing-dep /
// missing-schema.json paths exit 1 instead of 0. CI runs with --strict so a
// broken install or a forgotten `pnpm contracts:gen` FAILS the gate instead
// of silently passing. Dev/docker keeps the lenient default.

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');
const schemaPath = resolve(root, 'schema.json');

const strict = process.argv.includes('--strict') || process.env.CONTRACTS_STRICT === '1';

function skipOrFail(msg) {
  if (strict) {
    console.error(`[contracts] ${msg} (strict mode — failing instead of skipping).`);
    process.exit(1);
  }
  console.warn(`[contracts] ${msg} Skipping.`);
  process.exit(0);
}

let pySchema;
try {
  pySchema = JSON.parse(readFileSync(schemaPath, 'utf8'));
} catch {
  skipOrFail(`schema.json not found at ${schemaPath}; run \`pnpm contracts:gen\` first.`);
}

let registry;
try {
  const mod = await import('../src/ts/index.ts');
  registry = mod.REGISTRY;
} catch (err) {
  skipOrFail(`could not import zod schemas (${err?.message}).`);
}

let zodToJsonSchema;
try {
  ({ default: zodToJsonSchema } = await import('zod-to-json-schema'));
} catch {
  skipOrFail('zod-to-json-schema not installed (run `pnpm install`).');
}

const pyDefs = pySchema.$defs ?? {};
const names = Object.keys(registry);
let mismatches = 0;

for (const name of names) {
  const pyProps = pyDefs[name]?.properties ?? {};
  const pyKeys = Object.keys(pyProps).sort();

  const zodSchema = registry[name];
  const zj = zodToJsonSchema(zodSchema);
  const zodProps = zj.properties ?? {};
  const zodKeys = Object.keys(zodProps).sort();

  const same = pyKeys.length === zodKeys.length && pyKeys.every((k, i) => k === zodKeys[i]);

  if (!same) {
    mismatches++;
    console.error(`[contracts] DRIFT in ${name}:`);
    console.error(`  pydantic : ${pyKeys.join(', ') || '(none)'}`);
    console.error(`  zod      : ${zodKeys.join(', ') || '(none)'}`);
  }
}

// Also flag models present on one side only.
for (const pyName of Object.keys(pyDefs)) {
  if (!registry[pyName]) {
    mismatches++;
    console.error(`[contracts] DRIFT: ${pyName} exists in pydantic but not in zod REGISTRY`);
  }
}

if (mismatches > 0) {
  console.error(`[contracts] ${mismatches} drift(s) detected.`);
  process.exit(1);
}
console.log(`[contracts] OK — ${names.length} models in parity.`);
process.exit(0);
