// `streamChat` SSE body shape for family turns — the family_* fields MUST be
// serialized when set, MUST NOT be filtered out by the client, and the API
// endpoint must receive the same JSON the caller built. Personal vs family
// exclusivity is enforced by the server (400 if both blobs set — covered by
// the API test), so the client responsibility is just to forward what the
// caller asked for.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { streamChat } from '../lib/llm-client';

type Call = { url: string; init: RequestInit | undefined };

function stubFetchSSE() {
  const calls: Call[] = [];
  // Fake stream body: an empty ReadableStream that closes immediately, so
  // streamChat exits its read loop without ever blocking. We only assert
  // the request side here.
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"type":"done"}\n\n'));
      controller.close();
    },
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      calls.push({ url: _url, init });
      return {
        ok: true,
        status: 200,
        body: stream,
      } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('streamChat family body', () => {
  it('forwards family_id / visibility / participant_user_id / family_enc_key_blob / family_key_handle to the API', async () => {
    const calls = stubFetchSSE();
    await streamChat(
      {
        persona_id: 'fam',
        convo_id: 'c1',
        message: 'hi',
        family_id: 'fam-1',
        visibility: 'private',
        participant_user_id: 'u-owner',
        family_enc_key_blob: 'BLOB_FAM',
        family_key_handle: 'fam-kh-1',
      },
      { onEvent: () => {} },
    );
    expect(calls).toHaveLength(1);
    const init = calls[0]!.init!;
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.persona_id).toBe('fam');
    expect(body.family_id).toBe('fam-1');
    expect(body.visibility).toBe('private');
    expect(body.participant_user_id).toBe('u-owner');
    expect(body.family_enc_key_blob).toBe('BLOB_FAM');
    expect(body.family_key_handle).toBe('fam-kh-1');
    // Personal fields are NOT set — the family turn does not send them.
    expect(body.enc_key_blob).toBeUndefined();
  });

  it('forwards joint visibility=shared for joint turns', async () => {
    const calls = stubFetchSSE();
    await streamChat(
      {
        persona_id: 'fam',
        convo_id: 'fam-joint-abc',
        message: 'family meeting',
        family_id: 'fam-1',
        visibility: 'shared',
        participant_user_id: 'u-member',
        family_enc_key_blob: 'BLOB',
        family_key_handle: 'fam-kh-1',
      },
      { onEvent: () => {} },
    );
    const body = JSON.parse(calls[0]!.init!.body as string) as Record<string, unknown>;
    expect(body.visibility).toBe('shared');
    expect(body.convo_id).toBe('fam-joint-abc');
  });

  it('omits family_* fields on a personal (non-`fam`) turn', async () => {
    const calls = stubFetchSSE();
    await streamChat(
      {
        persona_id: 'aria',
        convo_id: 'c2',
        message: 'hi',
        enc_key_blob: 'BLOB_PERSONAL',
        key_handle: 'kh-1',
      },
      { onEvent: () => {} },
    );
    const body = JSON.parse(calls[0]!.init!.body as string) as Record<string, unknown>;
    expect(body.family_id).toBeUndefined();
    expect(body.family_enc_key_blob).toBeUndefined();
    expect(body.visibility).toBeUndefined();
    expect(body.participant_user_id).toBeUndefined();
    expect(body.enc_key_blob).toBe('BLOB_PERSONAL');
  });

  it('preserves both blobs at the client layer (server is the gate that 400s)', async () => {
    // The client is honest about what was sealed — it does not silently
    // drop one. The server enforces the mutual exclusion rule; the test
    // for that 400 lives in apps/api/tests/test_llm_stream.py.
    const calls = stubFetchSSE();
    await streamChat(
      {
        persona_id: 'fam',
        convo_id: 'c1',
        message: 'hi',
        enc_key_blob: 'BLOB_PERSONAL',
        family_enc_key_blob: 'BLOB_FAM',
        family_id: 'fam-1',
        family_key_handle: 'fam-kh-1',
      } as Parameters<typeof streamChat>[0],
      { onEvent: () => {} },
    );
    const body = JSON.parse(calls[0]!.init!.body as string) as Record<string, unknown>;
    expect(body.enc_key_blob).toBe('BLOB_PERSONAL');
    expect(body.family_enc_key_blob).toBe('BLOB_FAM');
  });
});
