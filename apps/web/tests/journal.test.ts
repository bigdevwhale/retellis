import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createJournalEntry,
  deleteJournalEntry,
  listJournalEntries,
  listJournalTags,
  updateJournalEntry,
} from '../lib/api-client';

// Thin client wrappers over /v1/journal. We stub global fetch and assert each
// function sends the right method + URL + query string + body (no network).
// Covers the query-string assembly for the list filters (q / tag / mood / from
// / to / limit / offset), the POST/PATCH bodies, and 204 handling on DELETE.

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

const ROW = {
  id: 'j1',
  user_id: 'u',
  persona_id: 'lou',
  title: null,
  body: 'quiet morning',
  mood: 'calm',
  tags: ['work'],
  salience: 0.66,
  source_convo_id: null,
  source_event_id: null,
  created_at: '2026-07-08T00:00:00Z',
  updated_at: '2026-07-08T00:00:00Z',
};

describe('journal client', () => {
  it('listJournalEntries builds the filter query string', async () => {
    const calls = stubFetch(200, [ROW]);
    const rows = await listJournalEntries({
      personaId: 'lou',
      q: 'quiet',
      tag: 'work',
      mood: 'calm',
      from: '2026-07-01T00:00:00Z',
      to: '2026-07-31T00:00:00Z',
      limit: 25,
      offset: 10,
    });
    expect(rows).toHaveLength(1);
    const qs = calls[0]!.url.split('?')[1] ?? '';
    expect(qs).toContain('persona_id=lou');
    expect(qs).toContain('q=quiet');
    expect(qs).toContain('tag=work');
    expect(qs).toContain('mood=calm');
    expect(qs).toContain('from=2026-07-01T00%3A00%3A00Z');
    expect(qs).toContain('to=2026-07-31T00%3A00%3A00Z');
    expect(qs).toContain('limit=25');
    expect(qs).toContain('offset=10');
    expect(calls[0]!.init?.method).toBeUndefined(); // GET
  });

  it('listJournalEntries issues a bare GET when no filters are given', async () => {
    const calls = stubFetch(200, []);
    await listJournalEntries();
    // No trailing "?" — no query params means no query string at all.
    expect(calls[0]!.url).toMatch(/\/v1\/journal$/);
  });

  it('listJournalTags builds the filter query string', async () => {
    const calls = stubFetch(200, { tags: ['work', 'family'] });
    const tags = await listJournalTags({
      personaId: 'lou',
      mood: 'calm',
      from: '2026-07-01T00:00:00Z',
      to: '2026-07-31T00:00:00Z',
      familyId: 'fam-1',
    });
    expect(tags).toEqual(['work', 'family']);
    const qs = calls[0]!.url.split('?')[1] ?? '';
    expect(qs).toContain('persona_id=lou');
    expect(qs).toContain('mood=calm');
    expect(qs).toContain('from=2026-07-01T00%3A00%3A00Z');
    expect(qs).toContain('to=2026-07-31T00%3A00%3A00Z');
    expect(qs).toContain('family_id=fam-1');
    // ``tag``/``q``/``limit``/``offset`` are NOT sent — the cloud is an
    // aggregate, not a list, and re-applying the active tag filter would
    // collapse the result to that one tag.
    expect(qs).not.toContain('tag=');
    expect(qs).not.toContain('q=');
    expect(qs).not.toContain('limit=');
    expect(qs).not.toContain('offset=');
    expect(calls[0]!.init?.method).toBeUndefined(); // GET
  });

  it('listJournalTags issues a bare GET when no filters are given', async () => {
    const calls = stubFetch(200, { tags: [] });
    const tags = await listJournalTags();
    expect(tags).toEqual([]);
    expect(calls[0]!.url).toMatch(/\/v1\/journal\/tags$/);
  });

  it('createJournalEntry POSTs the entry body', async () => {
    const calls = stubFetch(200, ROW);
    const out = await createJournalEntry({
      persona_id: 'lou',
      body: 'quiet morning',
      mood: 'calm',
      tags: ['work'],
      salience: 0.66,
      source_convo_id: 'c1',
    });
    expect(out.id).toBe('j1');
    const c = calls[0]!;
    expect(c.init?.method).toBe('POST');
    expect(c.url).toContain('/v1/journal');
    expect(JSON.parse(c.init?.body as string)).toEqual({
      persona_id: 'lou',
      body: 'quiet morning',
      mood: 'calm',
      tags: ['work'],
      salience: 0.66,
      source_convo_id: 'c1',
    });
  });

  it('updateJournalEntry PATCHes the partial body', async () => {
    const calls = stubFetch(200, { ...ROW, body: 'edited' });
    const out = await updateJournalEntry('j1', { body: 'edited', mood: null });
    expect(out.body).toBe('edited');
    const c = calls[0]!;
    expect(c.init?.method).toBe('PATCH');
    expect(c.url).toContain('/v1/journal/j1');
    // Explicit null must travel as JSON null (the server clears the column on
    // "field present + null", distinct from "field absent").
    expect(JSON.parse(c.init?.body as string)).toEqual({ body: 'edited', mood: null });
  });

  it('deleteJournalEntry DELETEs the id path and resolves on 204', async () => {
    const calls = stubFetch(204, undefined);
    const out = await deleteJournalEntry('j1');
    expect(out).toBeUndefined();
    const c = calls[0]!;
    expect(c.init?.method).toBe('DELETE');
    expect(c.url).toContain('/v1/journal/j1');
  });
});
