import { afterEach, describe, expect, it, vi } from 'vitest';
import { addMemoryShare, listMemoryShares, removeMemoryShare } from '../lib/api-client';

// Thin client wrappers over /v1/memory/shares. We stub global fetch and assert
// each function sends the right method + URL + body (no network). The DELETE
// path also covers the 204 No Content handling in jsonFetch.

type Call = { url: string; init: RequestInit | undefined };

function stubFetch(status: number, body: unknown) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      calls.push({ url: _url, init });
      const text = body === undefined ? '' : JSON.stringify(body);
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => (body === undefined ? undefined : body),
        text: async () => text,
      } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('memory share client', () => {
  it('listMemoryShares GETs the donor query', async () => {
    const calls = stubFetch(200, [
      {
        id: 's1',
        user_id: 'u',
        donor_persona_id: 'aria',
        receiver_persona_id: 'sam',
        created_at: '2026-07-07T00:00:00Z',
      },
    ]);
    const rows = await listMemoryShares('aria');
    expect(rows).toHaveLength(1);
    expect(rows[0]?.receiver_persona_id).toBe('sam');
    const c = calls[0]!;
    expect(c.init?.method).toBeUndefined(); // GET
    expect(c.url).toContain('/v1/memory/shares?donor_persona_id=aria');
  });

  it('addMemoryShare POSTs the donor/receiver body', async () => {
    const calls = stubFetch(200, {
      id: 's1',
      user_id: 'u',
      donor_persona_id: 'aria',
      receiver_persona_id: 'sam',
      created_at: '2026-07-07T00:00:00Z',
    });
    const share = await addMemoryShare('aria', 'sam');
    expect(share.id).toBe('s1');
    const c = calls[0]!;
    expect(c.init?.method).toBe('POST');
    expect(c.url).toContain('/v1/memory/shares');
    expect(JSON.parse(c.init?.body as string)).toEqual({
      donor_persona_id: 'aria',
      receiver_persona_id: 'sam',
    });
  });

  it('removeMemoryShare DELETEs with both ids and resolves on 204', async () => {
    const calls = stubFetch(204, undefined);
    const out = await removeMemoryShare('aria', 'sam');
    expect(out).toBeUndefined();
    const c = calls[0]!;
    expect(c.init?.method).toBe('DELETE');
    const qs = c.url.split('?')[1] ?? '';
    expect(qs).toContain('donor_persona_id=aria');
    expect(qs).toContain('receiver_persona_id=sam');
  });

  it('throws on a non-ok response', async () => {
    stubFetch(400, { detail: 'bad' });
    await expect(addMemoryShare('aria', 'aria')).rejects.toThrow();
  });
});
