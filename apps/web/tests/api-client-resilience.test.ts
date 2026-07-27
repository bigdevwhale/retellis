// @vitest-environment happy-dom

// I30 + I31: the API client must distinguish a *network* failure (no response
// at all) from an HTTP status, retry idempotent GETs once on a network blip,
// and never retry mutations. getMe must return null ONLY on 401 (so the auth
// gate can redirect to /login) and throw ApiError on a 5xx or network failure
// (so a transient server error during boot does NOT log a logged-in user out).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, getMe, isTransientOrNetworkError, listProviders } from '../lib/api-client';

const originalFetch = globalThis.fetch;

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.useRealTimers();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('jsonFetch — network failure retry (I30)', () => {
  it('retries a GET once on a network failure and returns the second attempt', async () => {
    const call = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse([{ id: 'p1' }]));

    globalThis.fetch = call as unknown as typeof fetch;

    const res = await listProviders();
    expect(res).toEqual([{ id: 'p1' }]);
    expect(call).toHaveBeenCalledTimes(2);
  });

  it('throws ApiError(null) after the GET retry also fails', async () => {
    const call = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    globalThis.fetch = call as unknown as typeof fetch;

    await expect(listProviders()).rejects.toMatchObject({
      name: 'ApiError',
      status: null,
      path: '/v1/providers',
    });
    // Exactly two attempts (initial + one retry), no more.
    expect(call).toHaveBeenCalledTimes(2);
  });
});

describe('jsonFetch — HTTP status (I30)', () => {
  it('throws ApiError with the status on a 5xx', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ detail: 'boom' }, 500),
    ) as unknown as typeof fetch;

    await expect(listProviders()).rejects.toMatchObject({
      name: 'ApiError',
      status: 500,
      path: '/v1/providers',
    });
  });

  it('throws ApiError with the status on a 4xx', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ detail: 'no' }, 404),
    ) as unknown as typeof fetch;

    await expect(listProviders()).rejects.toMatchObject({ status: 404 });
  });
});

describe('isTransientOrNetworkError (I30)', () => {
  it('treats network failure (null status) and 5xx as transient', () => {
    expect(isTransientOrNetworkError(new ApiError('/p', null))).toBe(true);
    expect(isTransientOrNetworkError(new ApiError('/p', 500))).toBe(true);
    expect(isTransientOrNetworkError(new ApiError('/p', 503))).toBe(true);
  });

  it('treats 4xx and non-ApiError as not transient', () => {
    expect(isTransientOrNetworkError(new ApiError('/p', 404))).toBe(false);
    expect(isTransientOrNetworkError(new ApiError('/p', 401))).toBe(false);
    expect(isTransientOrNetworkError(new Error('boom'))).toBe(false);
    expect(isTransientOrNetworkError(null)).toBe(false);
  });
});

describe('getMe — 401 vs 5xx vs network (I31)', () => {
  it('returns null on 401 (no session) so the gate may redirect', async () => {
    globalThis.fetch = vi.fn(
      async () => new Response('{"detail":"unauth"}', { status: 401 }),
    ) as unknown as typeof fetch;

    await expect(getMe()).resolves.toBeNull();
  });

  it('throws ApiError(500) on a 5xx so the gate does NOT redirect', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ detail: 'boom' }, 500),
    ) as unknown as typeof fetch;

    await expect(getMe()).rejects.toMatchObject({ name: 'ApiError', status: 500 });
  });

  it('retries once on a network failure, then throws ApiError(null)', async () => {
    const call = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'));

    globalThis.fetch = call as unknown as typeof fetch;

    await expect(getMe()).rejects.toMatchObject({ name: 'ApiError', status: null });
    expect(call).toHaveBeenCalledTimes(2);
  });

  it('returns the Principal when the retry succeeds after a network blip', async () => {
    const me = { user_id: 'u1', display_name: 'A', plan: 'free', credits_usd: 0 };
    const call = vi
      .fn<(...args: unknown[]) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse(me));

    globalThis.fetch = call as unknown as typeof fetch;

    await expect(getMe()).resolves.toEqual(me);
    expect(call).toHaveBeenCalledTimes(2);
  });
});
