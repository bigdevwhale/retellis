// @vitest-environment happy-dom

// Messenger API client — verifies the wire shapes hit the right endpoint,
// method, query, and body. The bot token never appears in a response the
// client parses (only bot_token_masked); the bind body carries the ECDH-sealed
// blob (or null for server fallback). 204 on delete → void.

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  bindTelegramBot,
  deleteMessenger,
  getMessengerStatus,
  initTelegramBot,
  listMessengers,
  patchMessenger,
} from '../lib/api-client';

const originalFetch = globalThis.fetch;

beforeEach(() => {
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

function lastCall(): { url: string; init: RequestInit } {
  const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1);
  return { url: String(call?.[0]), init: (call?.[1] as RequestInit) ?? {} };
}

const MESSENGER = {
  id: 'm1',
  user_id: 'u1',
  kind: 'telegram',
  status: 'active',
  persona_id: 'aria',
  chat_id: 42,
  bot_username: 'testbot',
  bot_token_masked: '…xxxx',
  byok_bound: true,
  last_error: null,
  last_seen_at: '2026-07-23T08:00:00Z',
  created_at: '2026-07-23T07:00:00Z',
  updated_at: '2026-07-23T08:00:00Z',
};

describe('messenger api-client', () => {
  it('GET /v1/messengers lists the user bots', async () => {
    globalThis.fetch = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockResolvedValueOnce(jsonResponse([MESSENGER])) as unknown as typeof fetch;

    const res = await listMessengers();
    expect(res).toHaveLength(1);
    expect(res[0]?.bot_token_masked).toBe('…xxxx');
    const { url, init } = lastCall();
    expect(url.endsWith('/v1/messengers')).toBe(true);
    expect(init.method ?? 'GET').toBe('GET');
  });

  it('POST /v1/messengers/telegram validates the token + persona in the body', async () => {
    globalThis.fetch = vi.fn<(...args: unknown[]) => Promise<Response>>().mockResolvedValueOnce(
      jsonResponse({
        messenger: MESSENGER,
        connect_token: 'tok',
        connect_url: 'https://x/connect/telegram?messenger=m1&token=tok',
        expires_at: '2026-07-23T08:10:00Z',
      }),
    ) as unknown as typeof fetch;

    const res = await initTelegramBot({
      bot_token: '123456789:AAExxxxxxxxxxxx',
      persona_id: 'aria',
    });
    expect(res.connect_token).toBe('tok');
    const { url, init } = lastCall();
    expect(url.endsWith('/v1/messengers/telegram')).toBe(true);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      bot_token: '123456789:AAExxxxxxxxxxxx',
      persona_id: 'aria',
    });
  });

  it('POST .../bind sends the connect token as a query param + the blob in the body', async () => {
    globalThis.fetch = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockResolvedValueOnce(jsonResponse(MESSENGER)) as unknown as typeof fetch;

    await bindTelegramBot('m1', 'tok', { byok_enc_key_blob: 'sealed-blob' });
    const { url, init } = lastCall();
    expect(url).toContain('/v1/messengers/telegram/m1/bind?');
    expect(url).toContain('token=tok');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ byok_enc_key_blob: 'sealed-blob' });
  });

  it('POST .../bind with null blob = server fallback', async () => {
    globalThis.fetch = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({ ...MESSENGER, byok_bound: false }),
      ) as unknown as typeof fetch;

    await bindTelegramBot('m1', 'tok', { byok_enc_key_blob: null });
    const { init } = lastCall();
    expect(JSON.parse(String(init.body))).toEqual({ byok_enc_key_blob: null });
  });

  it('PATCH /v1/messengers/:id updates persona/status', async () => {
    globalThis.fetch = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse({ ...MESSENGER, status: 'paused' }),
      ) as unknown as typeof fetch;

    await patchMessenger('m1', { status: 'paused' });
    const { url, init } = lastCall();
    expect(url.endsWith('/v1/messengers/m1')).toBe(true);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ status: 'paused' });
  });

  it('DELETE /v1/messengers/:id returns void on 204', async () => {
    globalThis.fetch = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockResolvedValueOnce(emptyResponse(204)) as unknown as typeof fetch;

    const res = await deleteMessenger('m1');
    expect(res).toBeUndefined();
    const { url, init } = lastCall();
    expect(url.endsWith('/v1/messengers/m1')).toBe(true);
    expect(init.method).toBe('DELETE');
  });

  it('GET /v1/messengers/:id/status returns the status snapshot', async () => {
    globalThis.fetch = vi.fn<(...args: unknown[]) => Promise<Response>>().mockResolvedValueOnce(
      jsonResponse({
        status: 'active',
        persona_id: 'aria',
        chat_id: 42,
        last_error: null,
        last_seen_at: '2026-07-23T08:00:00Z',
        byok_bound: true,
      }),
    ) as unknown as typeof fetch;

    const res = await getMessengerStatus('m1');
    expect(res.status).toBe('active');
    expect(res.byok_bound).toBe(true);
    const { url, init } = lastCall();
    expect(url.endsWith('/v1/messengers/m1/status')).toBe(true);
    expect(init.method ?? 'GET').toBe('GET');
  });
});
