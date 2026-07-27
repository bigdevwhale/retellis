'use client';

// SSE consumer for POST /v1/llm/stream. Parses the text/event-stream frame by
// frame (each `data:` line is a JSON event with a `type` discriminator) and
// yields typed events to the caller. Abortable via an AbortSignal so the chat
// composer can stop a stream mid-flight.

import type { LlmStreamEvent } from '@ai-companion/contracts';

// Same-origin relative by default (Caddy in prod, next.config rewrites in dev);
// NEXT_PUBLIC_API_URL is a cross-origin escape hatch. credentials:'include'
// carries the verified session cookie — see lib/api-client.ts for the rationale.

export type StreamHandlers = {
  onEvent: (evt: LlmStreamEvent) => void;
  onError?: (err: Error) => void;
};

export type StreamRequest = {
  persona_id: string;
  convo_id: string;
  message: string;
  enc_key_blob?: string | null;
  key_handle?: string | null;
  model?: string | null;
  // Embedding model from the active provider's `embeddings_model`. When set
  // alongside `enc_key_blob`, memory recall embeds semantically with the same
  // per-turn sealed key; the server silently falls back to the hash embedder
  // on any failure. Not secret — a model id, never a key.
  embeddings_model?: string | null;
  memory_on?: boolean;
  // Custom-persona override: the composed specialization/character/approach
  // prompt + tone sliders. Sent ONLY for user-built personas (persona.custom)
  // — builtins resolve on the server via the static registry. Not secret.
  persona_prompt?: string | null;
  persona_tone?: { warmth: number; direct: number; pace: number } | null;
  // Family scope (mutually exclusive with the personal key — see
  // routers/llm.py validations). `visibility` is the single source of truth on
  // the wire for what gets persisted AND what scopes recall: solo 1:1
  // ("private") reads shared + own private; joint ("shared") reads shared only.
  family_id?: string | null;
  visibility?: 'private' | 'shared' | null;
  participant_user_id?: string | null;
  // Family BYOK blob — the family owner's API key, ECDH-sealed in this member's
  // browser from the family vault (separate from the personal vault). Same
  // zero-knowledge path as `enc_key_blob`. The server returns 400 if both
  // `enc_key_blob` and `family_enc_key_blob` are set in the same turn.
  family_enc_key_blob?: string | null;
  family_key_handle?: string | null;
  // I8: optional client idempotency key. The server dedups persistence by
  // (user_id, convo_id, request_id) so a retried turn doesn't duplicate the
  // user+assistant events or fork the chain. A retry MUST reuse the original
  // turn's request_id for the dedup to apply; a fresh turn gets a fresh id.
  request_id?: string | null;
};

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? '').replace(/\/$/, '');

export async function streamChat(
  req: StreamRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/v1/llm/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(req),
    signal,
  });

  if (!res.ok || !res.body) {
    // Surface the real failure so the chat UI can tell a server-side 4xx/5xx
    // (e.g. the cross-family 404, a 400 validation, a 500) apart from a
    // network error / backend-not-running. FastAPI HTTPExceptions return a
    // JSON `{"detail": "..."}` body — read it so the caller sees the actual
    // reason ("family not found", "visibility=shared requires family_id",
    // …) instead of a bare status code. The thrown message format is
    // `llm/stream → {status}: {detail}` (detail omitted when absent).
    let detail = '';
    try {
      const txt = await res.text();
      try {
        const j = JSON.parse(txt) as { detail?: unknown };
        if (typeof j.detail === 'string') detail = j.detail;
        else if (j.detail !== undefined) detail = JSON.stringify(j.detail);
      } catch {
        detail = txt.slice(0, 200);
      }
    } catch {
      // Body unreadable (e.g. already consumed) — fall back to status only.
    }
    throw new Error(`llm/stream → ${res.status}${detail ? `: ${detail}` : ''}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line. Process complete frames only.
      let idx = buffer.indexOf('\n\n');
      while (idx >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        parseFrame(frame).forEach(handlers.onEvent);
        idx = buffer.indexOf('\n\n');
      }
    }
    // Flush any trailing frame.
    if (buffer.trim()) parseFrame(buffer).forEach(handlers.onEvent);
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    handlers.onError?.(err as Error);
    throw err;
  }
}

function parseFrame(frame: string): LlmStreamEvent[] {
  const events: LlmStreamEvent[] = [];
  for (const line of frame.split('\n')) {
    if (!line.startsWith('data:')) continue;
    const data = line.slice(5).trimStart();
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as LlmStreamEvent);
    } catch {
      // Ignore malformed keepalive/heartbeats.
    }
  }
  return events;
}
