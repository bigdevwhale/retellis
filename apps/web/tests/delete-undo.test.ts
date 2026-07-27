// I35: toast system + delete-conversation undo window. Covers:
//   - toast store push/subscribe/dismiss + auto-dismiss timer
//   - removeConvoFromList: optimistic removal, active-convo fallback, and the
//     empty-list placeholder (ChatScreen assumes convos[0] never undefined)
//   - restoreConvo: re-insert at prior index, restore active, drop placeholder
//   - deleteConvo: server-only, returns false on failure so the caller can
//     toast + restore (the honest limit — server-side memory may persist)

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api-client';
import { useStore } from '../lib/store';
import { dismiss, subscribe, toast } from '../lib/toast';

vi.mock('../lib/api-client', async (importOriginal) => {
  const real = await importOriginal<typeof api>();
  return {
    ...real,
    deleteConvoEvents: vi.fn(),
    listConversations: vi.fn(),
    listEvents: vi.fn(),
  };
});

const deleteConvoEvents = api.deleteConvoEvents as unknown as ReturnType<typeof vi.fn>;

function convo(id: string, personaId = 'aria') {
  return {
    id,
    personaId,
    title: { en: id, ru: id },
    ts: { en: 'now', ru: 'сейчас' },
    preview: { en: 'hi', ru: 'привет' },
    msgs: [{ them: true, t: { en: 'hi', ru: 'hi' }, ts: 'now' }],
  };
}

function seedConvos(ids: string[]) {
  const convos = ids.map((id) => convo(id));
  useStore.setState({
    convos,
    activeConvoId: convos[0]?.id ?? '',
    activePersonaId: 'aria',
    hydrated: true,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  deleteConvoEvents.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('toast store (I35)', () => {
  it('pushes, subscribes, and dismisses', () => {
    const seen: number[][] = [];
    const unsub = subscribe((items) => seen.push(items.map((t) => t.id)));
    const id = toast.success('saved');
    vi.advanceTimersByTime(0);
    expect(seen.at(-1)).toEqual([id]);
    dismiss(id);
    expect(seen.at(-1)).toEqual([]);
    unsub();
  });

  it('auto-dismisses after the default 5s (no action)', () => {
    let count = 0;
    const unsub = subscribe(() => {
      count++;
    });
    toast.error('boom');
    expect(count).toBeGreaterThan(0);
    const before = count;
    vi.advanceTimersByTime(4999);
    expect(count).toBe(before); // still there
    vi.advanceTimersByTime(2);
    expect(count).toBe(before + 1); // dismissed at 5s
    unsub();
  });

  it('keeps an action toast around longer (8s) so the user can click', () => {
    let current = 0;
    const unsub = subscribe((items) => {
      current = items.length;
    });
    const id = toast.info('deleted', { action: { label: 'Undo', onClick: () => {} } });
    expect(current).toBe(1);
    vi.advanceTimersByTime(5000);
    // Still present at 5s (a no-action toast would be gone by now).
    expect(current).toBe(1);
    vi.advanceTimersByTime(3000);
    expect(current).toBe(0); // gone at 8s
    void id;
    unsub();
  });
});

describe('removeConvoFromList (I35)', () => {
  it('removes the convo and returns a token', () => {
    seedConvos(['a', 'b', 'c']);
    const token = useStore.getState().removeConvoFromList('b');
    expect(token).not.toBeNull();
    expect(useStore.getState().convos.map((c) => c.id)).toEqual(['a', 'c']);
    expect(token?.convo.id).toBe('b');
    expect(token?.index).toBe(1);
    expect(token?.placeholderId).toBeNull();
  });

  it('falls back to the first remaining convo when the active one is removed', () => {
    seedConvos(['a', 'b', 'c']);
    useStore.setState({ activeConvoId: 'a' });
    useStore.getState().removeConvoFromList('a');
    expect(useStore.getState().activeConvoId).toBe('b');
  });

  it('seeds a placeholder when the removal would empty the list', () => {
    seedConvos(['only']);
    const token = useStore.getState().removeConvoFromList('only');
    expect(token?.placeholderId).not.toBeNull();
    expect(useStore.getState().convos).toHaveLength(1);
    expect(useStore.getState().convos[0]?.id).toBe(token?.placeholderId);
    // ChatScreen reads convos[0] — must never be undefined.
    expect(useStore.getState().convos[0]).toBeDefined();
  });

  it('returns null for an unknown id', () => {
    seedConvos(['a']);
    expect(useStore.getState().removeConvoFromList('nope')).toBeNull();
  });
});

describe('restoreConvo (I35)', () => {
  it('re-inserts at the prior index and makes it active again', () => {
    seedConvos(['a', 'b', 'c']);
    const token = useStore.getState().removeConvoFromList('b')!;
    useStore.getState().restoreConvo(token);
    expect(useStore.getState().convos.map((c) => c.id)).toEqual(['a', 'b', 'c']);
    expect(useStore.getState().activeConvoId).toBe('b');
  });

  it('drops the placeholder that was seeded to keep the list non-empty', () => {
    seedConvos(['only']);
    const token = useStore.getState().removeConvoFromList('only')!;
    expect(token.placeholderId).not.toBeNull();
    // A placeholder is now present.
    expect(useStore.getState().convos.map((c) => c.id)).toEqual([token.placeholderId]);
    useStore.getState().restoreConvo(token);
    // Placeholder gone, original back.
    expect(useStore.getState().convos.map((c) => c.id)).toEqual(['only']);
  });
});

describe('deleteConvo server half (I35)', () => {
  it('returns true on a successful server delete', async () => {
    seedConvos(['a']);
    const ok = await useStore.getState().deleteConvo(convo('a'));
    expect(ok).toBe(true);
    expect(deleteConvoEvents).toHaveBeenCalledWith('aria', 'a');
  });

  it('returns false on a server failure so the caller can toast + restore', async () => {
    deleteConvoEvents.mockRejectedValueOnce(new Error('network'));
    seedConvos(['a']);
    const ok = await useStore.getState().deleteConvo(convo('a'));
    expect(ok).toBe(false);
  });

  it('does not mutate the list (optimistic removal owns that)', async () => {
    seedConvos(['a', 'b']);
    await useStore.getState().deleteConvo(convo('a'));
    // List untouched — the caller already removed it optimistically.
    expect(useStore.getState().convos.map((c) => c.id)).toEqual(['a', 'b']);
  });
});
