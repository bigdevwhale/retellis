// Family slice in the Zustand store: setters, hydration (loadFamily), and the
// scope-pure convo-id minting rule (a family `fam` convo never mixes solo /
// joint, never crosses families). We stub `getFamily` so no network.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api-client';
import { useStore } from '../lib/store';

vi.mock('../lib/api-client', async (importOriginal) => {
  const real = await importOriginal<typeof api>();
  return {
    ...real,
    getFamily: vi.fn(),
    // loadFamily() also fetches the family therapist prompt in parallel; the
    // family-store tests don't exercise the prompt tab, so a default-null
    // response is correct.
    getFamilyTherapistPrompt: vi.fn(async () => ({
      body: null,
      set_by_user_id: null,
      set_at: null,
      set_by_display_name: null,
    })),
  };
});

const mockedGetFamily = api.getFamily as unknown as ReturnType<typeof vi.fn>;

const FAMILY_STATE = {
  family: {
    id: 'fam-1',
    name: 'Test',
    owner_user_id: 'u-owner',
    created_at: '2026-07-09T00:00:00Z',
    family_salt: 'AAA=',
    family_enc_blob_seed: null,
    use_owner_personal_key: false,
  },
  members: [
    {
      family_id: 'fam-1',
      user_id: 'u-owner',
      family_role: 'owner' as const,
      family_display_name: 'Alex',
      relation: 'parent',
      color: '#fff',
      joined_at: '2026-07-09T00:00:00Z',
    },
    {
      family_id: 'fam-1',
      user_id: 'u-member',
      family_role: 'member' as const,
      family_display_name: 'Sam',
      relation: 'child',
      color: '#000',
      joined_at: '2026-07-09T00:00:00Z',
    },
  ],
  invites: [],
  provider: null,
};

beforeEach(() => {
  // Reset the family slice to defaults between tests so the cases are
  // independent.
  useStore.setState({
    family: null,
    familyMembers: [],
    familyInvites: [],
    familyProvider: null,
    activeFamilyMemberId: null,
    familySessionMode: 'private',
  });
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('family slice setters', () => {
  it('setFamily + setFamilyMembers + setActiveFamilyMemberId', () => {
    useStore.getState().setFamily(FAMILY_STATE.family);
    useStore.getState().setFamilyMembers(FAMILY_STATE.members);
    useStore.getState().setActiveFamilyMemberId('u-member');
    const s = useStore.getState();
    expect(s.family?.id).toBe('fam-1');
    expect(s.familyMembers).toHaveLength(2);
    expect(s.activeFamilyMemberId).toBe('u-member');
  });

  it('setFamilySessionMode defaults to private and toggles to shared', () => {
    expect(useStore.getState().familySessionMode).toBe('private');
    useStore.getState().setFamilySessionMode('shared');
    expect(useStore.getState().familySessionMode).toBe('shared');
  });
});

describe('loadFamily', () => {
  it('hydrates the slice from getFamily and defaults activeFamilyMemberId to me', async () => {
    mockedGetFamily.mockResolvedValueOnce(FAMILY_STATE);
    await useStore.getState().loadFamily('u-owner');
    const s = useStore.getState();
    expect(s.family?.id).toBe('fam-1');
    expect(s.familyMembers).toHaveLength(2);
    // The picker defaults to the caller; the user can switch.
    expect(s.activeFamilyMemberId).toBe('u-owner');
    expect(s.familyInvites).toEqual([]);
    expect(s.familyProvider).toBeNull();
  });

  it('on 404 (not in a family) clears the slice but does not throw', async () => {
    mockedGetFamily.mockRejectedValueOnce(new Error('404 not found'));
    await expect(useStore.getState().loadFamily('u-stranger')).resolves.toBeUndefined();
    const s = useStore.getState();
    expect(s.family).toBeNull();
    expect(s.familyMembers).toEqual([]);
    expect(s.activeFamilyMemberId).toBeNull();
  });

  it('on a non-404 network blip keeps the previous slice intact (next call retries)', async () => {
    useStore.getState().setFamily(FAMILY_STATE.family);
    mockedGetFamily.mockRejectedValueOnce(new Error('network down'));
    await useStore.getState().loadFamily('u-owner');
    expect(useStore.getState().family?.id).toBe('fam-1');
  });
});

describe('fam convo scope-pure id minting', () => {
  // The id encodes the scope so a stale reference can't land in the wrong
  // predicate. See PLAN §Family, "convo never mixes scopes".
  it('solo `fam` convo id starts with `fam-solo-`', () => {
    useStore.getState().setFamily(FAMILY_STATE.family);
    useStore.getState().setFamilySessionMode('private');
    const id = useStore.getState().newChat('fam');
    expect(id).toMatch(/^fam-solo-/);
  });

  it('joint `fam` convo id starts with `fam-joint-`', () => {
    useStore.getState().setFamily(FAMILY_STATE.family);
    useStore.getState().setFamilySessionMode('shared');
    const id = useStore.getState().newChat('fam');
    expect(id).toMatch(/^fam-joint-/);
  });

  it('joint `fam` convo id is deterministic per family and create-or-reuse', () => {
    // Joint session = ONE shared convo per family. The id is derived from the
    // family id (not a per-mint timestamp), so every member lands on the same
    // server thread, and a second newChat reuses the existing stub rather than
    // duplicating it.
    useStore.getState().setFamily(FAMILY_STATE.family);
    useStore.getState().setFamilySessionMode('shared');
    const id1 = useStore.getState().newChat('fam');
    const countAfterFirst = useStore.getState().convos.filter((c) => c.id === id1).length;
    const id2 = useStore.getState().newChat('fam');
    expect(id1).toBe(`fam-joint-${FAMILY_STATE.family.id}`);
    expect(id2).toBe(id1); // deterministic, not a fresh mint
    // Reuse: the second call must NOT append a duplicate convo stub.
    expect(useStore.getState().convos.filter((c) => c.id === id1)).toHaveLength(countAfterFirst);
    expect(useStore.getState().convos.filter((c) => c.id === id1)).toHaveLength(1);
  });

  it('joint `fam` convo stub starts EMPTY so server history loads (not the local greeting)', () => {
    // The joint thread's messages live on the server under every member's
    // user_id. If newChat seeded the local greeting (msgs.length > 0),
    // loadConvoMessages would skip the server fetch and a member entering the
    // joint chat would see only the greeting, not the other members' messages.
    // Solo keeps the greeting (a personal new thread, harmless placeholder).
    useStore.getState().setFamily(FAMILY_STATE.family);
    useStore.getState().setFamilySessionMode('shared');
    const jointId = useStore.getState().newChat('fam');
    const joint = useStore.getState().convos.find((c) => c.id === jointId)!;
    expect(joint.msgs).toEqual([]); // empty → loadConvoMessages will fetch

    useStore.getState().setFamilySessionMode('private');
    const soloId = useStore.getState().newChat('fam');
    const solo = useStore.getState().convos.find((c) => c.id === soloId)!;
    expect(solo.msgs.length).toBeGreaterThan(0); // greeting seeded
  });

  it('non-`fam` persona does not get the family prefix even when in a family', () => {
    useStore.getState().setFamily(FAMILY_STATE.family);
    const id = useStore.getState().newChat('aria');
    expect(id).not.toMatch(/^fam-/);
  });

  it('no family → `fam` persona falls back to a regular convo id', () => {
    // (E.g. the fixtures loaded a fam persona but the user hasn't created a
    // family yet — the convo still has to mint something.)
    const id = useStore.getState().newChat('fam');
    expect(id).not.toMatch(/^fam-/);
  });
});
