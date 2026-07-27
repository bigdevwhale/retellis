import { describe, expect, it } from 'vitest';

// Pure unit tests of the per-provider catalog. No vault or API surface —
// just the shape Open Design's apiProtocols.ts carries, ported to TS.

import {
  FIXED_ORIGIN_KINDS,
  PROVIDER_CATALOG,
  PROVIDER_ORDER,
  defaultModel,
  hasEmbeddings,
  isFixedOriginKind,
  providerMeta,
  resolveEffectiveBaseUrl,
  suggestedModels,
} from '@/lib/providerCatalog';

describe('providerCatalog', () => {
  it('exposes the 8 Open Design kinds in the canonical order', () => {
    expect(PROVIDER_ORDER).toEqual([
      'openai',
      'anthropic',
      'google',
      'openrouter',
      'ollama',
      'azure',
      'aihubmix',
      'bedrock',
    ]);
    // Every kind in the order has a meta record, and vice versa.
    for (const k of PROVIDER_ORDER) {
      expect(PROVIDER_CATALOG[k]).toBeDefined();
    }
    expect(Object.keys(PROVIDER_CATALOG).sort()).toEqual([...PROVIDER_ORDER].sort());
  });

  it('matches Open Design default models for the original 5 kinds', () => {
    expect(defaultModel('openai')).toBe('gpt-4o-mini');
    expect(defaultModel('anthropic')).toBe('claude-3-5-haiku-latest');
    expect(defaultModel('google')).toBe('gemini/gemini-1.5-flash');
    expect(defaultModel('openrouter')).toBe('openrouter/anthropic/claude-3.5-haiku');
    expect(defaultModel('ollama')).toBe('ollama/llama3.3');
  });

  it('sets sensible defaults for the 3 new kinds', () => {
    expect(defaultModel('azure')).toBe('azure/gpt-4o-mini');
    expect(defaultModel('aihubmix')).toBe('gpt-4o-mini');
    expect(defaultModel('bedrock')).toBe('bedrock/anthropic.claude-3-5-haiku-20241022-v1:0');
  });

  it('keeps per-kind curated model lists non-empty and includes the default', () => {
    for (const k of PROVIDER_ORDER) {
      const meta = providerMeta(k);
      expect(meta.suggestedModels.length).toBeGreaterThan(0);
      expect(meta.suggestedModels).toContain(meta.defaultModel);
    }
  });

  it('marks only AIHubMix as a fixed-origin gateway (no base_url field)', () => {
    expect([...FIXED_ORIGIN_KINDS]).toEqual(['aihubmix']);
    expect(isFixedOriginKind('aihubmix')).toBe(true);
    expect(isFixedOriginKind('openai')).toBe(false);
    expect(isFixedOriginKind('bedrock')).toBe(false);
  });

  it('resolveEffectiveBaseUrl forces the canonical URL for fixed-origin kinds', () => {
    // Empty user value → still use the canonical URL (never leak the empty
    // string up the stack — server-side URL gates would break otherwise).
    expect(resolveEffectiveBaseUrl('aihubmix', '')).toBe('https://aihubmix.com/v1');
    expect(resolveEffectiveBaseUrl('aihubmix', null)).toBe('https://aihubmix.com/v1');
    // The user value is also discarded for fixed-origin kinds.
    expect(resolveEffectiveBaseUrl('aihubmix', 'https://attacker.example/v1')).toBe(
      'https://aihubmix.com/v1',
    );
  });

  it('resolveEffectiveBaseUrl returns the user value for non-fixed-origin kinds', () => {
    expect(resolveEffectiveBaseUrl('openai', 'https://api.openai.com/v1')).toBe(
      'https://api.openai.com/v1',
    );
    // Empty / null are passed through unchanged — the backend treats empty as
    // "use the provider's default".
    expect(resolveEffectiveBaseUrl('openai', '')).toBe('');
    expect(resolveEffectiveBaseUrl('openai', null)).toBe('');
  });

  it('gates the embeddings field to the 5 kinds that actually expose an embeddings API', () => {
    expect(hasEmbeddings('openai')).toBe(true);
    expect(hasEmbeddings('azure')).toBe(true);
    expect(hasEmbeddings('google')).toBe(true);
    expect(hasEmbeddings('ollama')).toBe(true);
    expect(hasEmbeddings('aihubmix')).toBe(true);
    // No first-party embeddings API on Anthropic / OpenRouter / Bedrock.
    expect(hasEmbeddings('anthropic')).toBe(false);
    expect(hasEmbeddings('openrouter')).toBe(false);
    expect(hasEmbeddings('bedrock')).toBe(false);
  });

  it('flags Bedrock as the only kind with the aws credential shape', () => {
    expect(providerMeta('bedrock').credentialShape).toBe('aws');
    for (const k of PROVIDER_ORDER) {
      if (k === 'bedrock') continue;
      expect(providerMeta(k).credentialShape ?? 'single').toBe('single');
    }
  });

  it('supplies a console link + placeholder for every kind', () => {
    for (const k of PROVIDER_ORDER) {
      const meta = providerMeta(k);
      expect(meta.apiKeyConsoleUrl).toMatch(/^https:\/\//);
      expect(meta.apiKeyPlaceholder.length).toBeGreaterThan(0);
    }
  });

  it('suggestedModels helper matches providerMeta(...).suggestedModels', () => {
    for (const k of PROVIDER_ORDER) {
      expect(suggestedModels(k)).toEqual(providerMeta(k).suggestedModels);
    }
  });

  // The form's submit contract — ``ProviderKeyFormValues`` — derives its
  // shape from the catalog: ``extra`` is non-null only when the kind's
  // credential shape is 'aws' (Bedrock); ``baseUrl`` collapses to the
  // canonical URL for fixed-origin kinds; ``embeddingsModel`` is null when
  // the kind has no embeddings API. Tested via the catalog + the values the
  // form would build, not via render (no RTL in this codebase).
  it('form contract: extra is non-null only for Bedrock', () => {
    for (const k of PROVIDER_ORDER) {
      const wantsExtra = providerMeta(k).credentialShape === 'aws';
      if (wantsExtra) {
        expect(k).toBe('bedrock');
      } else {
        expect(providerMeta(k).credentialShape ?? 'single').toBe('single');
      }
    }
  });

  it('form contract: every kind has a sensible default model to pre-fill', () => {
    for (const k of PROVIDER_ORDER) {
      const m = providerMeta(k);
      expect(m.defaultModel.length).toBeGreaterThan(0);
      // OpenAI-compatible gateway ids (openai, ollama, aihubmix) match the
      // kind's vendor prefix or are bare OpenAI ids.
      if (k === 'openai') expect(m.defaultModel).toMatch(/^gpt-/);
      if (k === 'anthropic') expect(m.defaultModel).toMatch(/^claude-/);
      if (k === 'google') expect(m.defaultModel).toMatch(/^gemini\//);
      if (k === 'openrouter') expect(m.defaultModel).toMatch(/^openrouter\//);
      if (k === 'ollama') expect(m.defaultModel).toMatch(/^ollama\//);
      if (k === 'azure') expect(m.defaultModel).toMatch(/^azure\//);
      if (k === 'bedrock') expect(m.defaultModel).toMatch(/^bedrock\//);
    }
  });
});
