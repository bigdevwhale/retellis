// Single source of truth for the BYOK provider picker — mirrors Open Design's
// `apps/web/src/providers/apiProtocols.ts` shape so the two surfaces (BYOK
// modal here, the litellm chain in the API) agree on the same eight provider
// kinds, default models, key placeholders, and "fixed-origin gateway" handling.
//
// The 8 kinds are the same eight the upstream open-design BYOK picker ships:
//   openai, anthropic, google, openrouter, ollama — the original five
//   azure, aihubmix, bedrock — three new ones, gated on litellm support
//   (azure needs api_base+api_version; aihubmix is an OpenAI-compatible
//   fixed-origin gateway; bedrock uses an AWS access-key + secret + region
//   triplet, not a single Bearer key).
//
// The `senseaudio` provider from the upstream picker is intentionally NOT
// carried over here — it can be added later as another fixed-origin OpenAI-
// compatible gateway, no schema change required.

import type { ProviderKind as ContractsProviderKind } from '@ai-companion/contracts';

// Re-export the contracts type under the local name so existing call sites
// (`import type { ProviderKind } from './fixtures'`) keep type-checking.
export type ProviderKind = ContractsProviderKind;

// Localized kind label + description shown on the picker card / tab.
export type ProviderMeta = {
  kind: ProviderKind;
  label: string;
  desc: { en: string; ru: string };
  // Default base URL the picker pre-fills in the Endpoint field. Empty for
  // gateways where the user enters a deployment-specific endpoint (Azure) or
  // for fixed-origin gateways (AIHubMix — see FIXED_ORIGIN_KINDS).
  defaultBaseUrl: string;
  // Placeholder for the API key input. Helps the user pick the right key off
  // their provider dashboard.
  apiKeyPlaceholder: string;
  // Right-aligned "Get API key" link shown on the key field. The label is in
  // i18n (`byok.get_api_key`); the URL is the destination.
  apiKeyConsoleUrl: string;
  // Default litellm model id for this kind when the user hasn't picked one.
  // The first entry of `suggestedModels` is the same value — kept here so the
  // empty state ("just use a default") can pre-fill without scanning a list.
  defaultModel: string;
  // Curated list the model combobox shows as dropdown options. The first
  // entry is the default; the "Custom…" option always sits below the list.
  suggestedModels: readonly string[];
  // "Fast / cheap" sibling — used by the memory extractor's auto-mode pill
  // and any other place that wants a single sensible default for utility
  // calls. Falls back to `defaultModel` when unset.
  fastModel?: string;
  // Whether the kind has an embeddings API. When set, the embeddings field
  // is shown on the form and the BYOK recall path embeds with the user's
  // own sealed key (same precedence as chat: BYOK → env → hash). When
  // absent, the embeddings field is hidden — those kinds are pure chat.
  embeddingsDefault?: string;
  // Whether the form should hide the Endpoint URL field. AIHubMix is a
  // fixed-origin OpenAI-compatible gateway; the Base URL is implied, so the
  // user only supplies the key.
  fixedOrigin?: boolean;
  // Set when this kind needs fields the simple "API key" model can't carry.
  // Bedrock needs AWS access key + secret + region; the picker renders an
  // extra sub-form.
  credentialShape?: 'single' | 'aws';
};

export const PROVIDER_CATALOG: Record<ProviderKind, ProviderMeta> = {
  openai: {
    kind: 'openai',
    label: 'OpenAI',
    desc: {
      en: 'gpt-4o · gpt-4o-mini · o3',
      ru: 'gpt-4o · gpt-4o-mini · o3',
    },
    defaultBaseUrl: 'https://api.openai.com/v1',
    apiKeyPlaceholder: 'sk-...',
    apiKeyConsoleUrl: 'https://platform.openai.com/api-keys',
    defaultModel: 'gpt-4o-mini',
    suggestedModels: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1', 'o3', 'o4-mini'],
    fastModel: 'gpt-4o-mini',
    embeddingsDefault: 'text-embedding-3-small',
  },
  anthropic: {
    kind: 'anthropic',
    label: 'Anthropic',
    desc: {
      en: 'Claude Haiku · Sonnet',
      ru: 'Claude Haiku · Sonnet',
    },
    defaultBaseUrl: 'https://api.anthropic.com',
    apiKeyPlaceholder: 'sk-ant-...',
    apiKeyConsoleUrl: 'https://console.anthropic.com/settings/keys',
    defaultModel: 'claude-3-5-haiku-latest',
    suggestedModels: [
      'claude-3-5-haiku-latest',
      'claude-3-5-sonnet-latest',
      'claude-3-opus-latest',
    ],
    fastModel: 'claude-3-5-haiku-latest',
    // Anthropic has no first-party embeddings API at the time of writing;
    // the field is hidden for this kind.
  },
  google: {
    kind: 'google',
    label: 'Google',
    desc: {
      en: 'Gemini 1.5 / 2.0',
      ru: 'Gemini 1.5 / 2.0',
    },
    defaultBaseUrl: 'https://generativelanguage.googleapis.com/v1beta',
    apiKeyPlaceholder: 'AIza...',
    apiKeyConsoleUrl: 'https://aistudio.google.com/apikey',
    defaultModel: 'gemini/gemini-1.5-flash',
    suggestedModels: [
      'gemini/gemini-1.5-flash',
      'gemini/gemini-1.5-pro',
      'gemini/gemini-2.0-flash',
      'gemini/gemini-2.5-flash',
    ],
    fastModel: 'gemini/gemini-1.5-flash',
    embeddingsDefault: 'gemini/gemini-embedding-001',
  },
  openrouter: {
    kind: 'openrouter',
    label: 'OpenRouter',
    desc: {
      en: 'many models',
      ru: 'много моделей',
    },
    defaultBaseUrl: 'https://openrouter.ai/api/v1',
    apiKeyPlaceholder: 'sk-or-...',
    apiKeyConsoleUrl: 'https://openrouter.ai/settings/keys',
    defaultModel: 'openrouter/anthropic/claude-3.5-haiku',
    suggestedModels: [
      'openrouter/anthropic/claude-3.5-haiku',
      'openrouter/anthropic/claude-3.5-sonnet',
      'openrouter/openai/gpt-4o-mini',
      'openrouter/openai/gpt-4o',
      'openrouter/google/gemini-2.0-flash-001',
    ],
    fastModel: 'openrouter/anthropic/claude-3.5-haiku',
    // OpenRouter doesn't host embeddings; field is hidden.
  },
  ollama: {
    kind: 'ollama',
    label: 'Ollama',
    desc: {
      en: 'local · offline · Cloud',
      ru: 'локально · офлайн · Cloud',
    },
    // Empty by default — local Ollama runs at http://localhost:11434 with no
    // key; the form's help text walks the user through it. Ollama Cloud
    // (https://ollama.com) requires a key; the placeholder is for the key,
    // not the endpoint.
    defaultBaseUrl: '',
    apiKeyPlaceholder: 'Ollama API key',
    apiKeyConsoleUrl: 'https://ollama.com/settings/keys',
    defaultModel: 'ollama/llama3.3',
    suggestedModels: ['ollama/llama3.3', 'ollama/qwen2.5', 'ollama/mistral', 'ollama/phi3'],
    fastModel: 'ollama/llama3.3',
    embeddingsDefault: 'ollama/nomic-embed-text',
  },
  aihubmix: {
    kind: 'aihubmix',
    label: 'AIHubMix',
    desc: {
      en: 'OpenAI-compatible gateway',
      ru: 'OpenAI-совместимый шлюз',
    },
    // Fixed-origin: the picker hides the Endpoint URL field for this kind
    // and uses this URL implicitly.
    defaultBaseUrl: 'https://aihubmix.com/v1',
    apiKeyPlaceholder: 'sk-...',
    apiKeyConsoleUrl: 'https://aihubmix.com/dashboard',
    defaultModel: 'gpt-4o-mini',
    suggestedModels: [
      'gpt-4o-mini',
      'gpt-4o',
      'claude-3-5-sonnet-latest',
      'gemini-2.0-flash',
      'deepseek-chat',
    ],
    fastModel: 'gpt-4o-mini',
    // AIHubMix exposes an OpenAI-compatible /v1/embeddings; kept enabled so
    // semantic memory works through the same gateway the chat uses.
    embeddingsDefault: 'text-embedding-3-small',
    fixedOrigin: true,
  },
  azure: {
    kind: 'azure',
    label: 'Azure OpenAI',
    desc: {
      en: 'Azure OpenAI Service',
      ru: 'Azure OpenAI Service',
    },
    // Azure requires the resource endpoint as the base URL — no sensible
    // default, the user must paste it from the Azure portal.
    defaultBaseUrl: '',
    apiKeyPlaceholder: 'azure key',
    apiKeyConsoleUrl: 'https://oai.azure.com/portal/',
    // Deployment name + prefix; the form's help text tells the user to
    // type their deployment id (e.g. ``gpt-4o-mini``).
    defaultModel: 'azure/gpt-4o-mini',
    suggestedModels: ['azure/gpt-4o-mini', 'azure/gpt-4o', 'azure/gpt-4.1-mini'],
    fastModel: 'azure/gpt-4o-mini',
    embeddingsDefault: 'azure/text-embedding-3-small',
  },
  bedrock: {
    kind: 'bedrock',
    label: 'AWS Bedrock',
    desc: {
      en: 'AWS credential triplet',
      ru: 'тройка AWS-ключей',
    },
    // Region is part of the credential sub-form, not the Endpoint URL.
    defaultBaseUrl: '',
    // Bedrock's "API key" is the AWS access key id; the secret + region
    // travel in the credential sub-form.
    apiKeyPlaceholder: 'AKIA...',
    apiKeyConsoleUrl: 'https://console.aws.amazon.com/iam/home#/security_credentials',
    defaultModel: 'bedrock/anthropic.claude-3-5-haiku-20241022-v1:0',
    suggestedModels: [
      'bedrock/anthropic.claude-3-5-haiku-20241022-v1:0',
      'bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0',
      'bedrock/amazon.nova-lite-v1:0',
      'bedrock/amazon.nova-pro-v1:0',
    ],
    fastModel: 'bedrock/amazon.nova-lite-v1:0',
    // No Bedrock-hosted embeddings model on the user side; field hidden.
    credentialShape: 'aws',
  },
};

// Display order in the picker tabs. Matches Open Design's tab order.
export const PROVIDER_ORDER: readonly ProviderKind[] = [
  'openai',
  'anthropic',
  'google',
  'openrouter',
  'ollama',
  'azure',
  'aihubmix',
  'bedrock',
];

export const FIXED_ORIGIN_KINDS: ReadonlySet<ProviderKind> = new Set<ProviderKind>(
  // The list of kinds that hide the Endpoint URL field (fixed-origin
  // OpenAI-compatible gateways and the AWS-shaped credential kinds that
  // don't carry a base URL at all). Cast to ProviderKind so the Set's
  // generic is the union, not the narrower literal type.
  ['aihubmix'] as readonly ProviderKind[],
);

export function isFixedOriginKind(kind: ProviderKind): boolean {
  return FIXED_ORIGIN_KINDS.has(kind);
}

export function providerMeta(kind: ProviderKind): ProviderMeta {
  return PROVIDER_CATALOG[kind];
}

// Resolve the effective base URL the BYOK picker should send to the API.
// Fixed-origin gateways always use their canonical origin; an empty stored
// value would otherwise leak through and break URL-gated server logic
// (live model-list fetch, etc). For other kinds the user value (possibly
// empty) wins.
export function resolveEffectiveBaseUrl(
  kind: ProviderKind,
  baseUrl: string | null | undefined,
): string {
  if (isFixedOriginKind(kind)) return PROVIDER_CATALOG[kind].defaultBaseUrl;
  return (baseUrl ?? '').trim();
}

export function defaultModel(kind: ProviderKind): string {
  return PROVIDER_CATALOG[kind].defaultModel;
}

export function suggestedModels(kind: ProviderKind): readonly string[] {
  return PROVIDER_CATALOG[kind].suggestedModels;
}

// True when this kind exposes an embeddings endpoint the user can enable.
// The picker renders the embeddings field only for these.
export function hasEmbeddings(kind: ProviderKind): boolean {
  return Boolean(PROVIDER_CATALOG[kind].embeddingsDefault);
}
