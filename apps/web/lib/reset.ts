// Reset / wipe helpers for the personal and family BYOK keys. The client-side
// vault is gone — keys live server-side, envelope-encrypted — so a "reset" is
// now a pure server-side delete: list the provider rows, delete each, and
// clear the zustand display state. No local IndexedDB wipe (none exists).
// The destructive-confirm UX lives in the screens; this module is the single
// shared destructive path so the chat banner, onboarding, and family settings
// cannot drift.

import {
  clearFamilyVaultSeed,
  deleteFamilyProvider,
  deleteProvider,
  listFamilyProviders,
  listProviders,
} from './api-client';
import { useStore } from './store';

export interface ResetResult {
  // Number of family provider rows that were deleted on the server side.
  // ``0`` is a valid outcome (no provider was configured).
  providersDeleted: number;
  // ``true`` if the legacy family vault metadata was cleared on the family
  // row. ``false`` if the server call failed — see error path.
  serverSeedOk: boolean;
}

/** Wipe and re-onboard path for the personal BYOK keys. Deletes every
 * server-side provider row for this user and clears the zustand
 * ``activeProvider`` display pointer. No local state to wipe (the
 * client-side vault is gone). */
export async function resetPersonalVault(): Promise<void> {
  try {
    const remote = await listProviders();
    await Promise.all(remote.map((p) => deleteProvider(p.id)));
  } finally {
    // Always clear the in-memory zustand slice so a partially-failed reset
    // doesn't leave the user staring at a stale ``activeProvider`` that no
    // longer matches the server.
    useStore.getState().setActiveProvider(null);
  }
}

/** Owner-only. Destructive. Drops the family provider row(s) on the server
 * and clears the legacy family vault metadata (family_salt +
 * family_enc_blob_seed) on the families row so the owner can re-add a family
 * key from /family.
 *
 * Order: server provider deletes first, then the family vault metadata clear.
 * If the server-side calls fail, the user can re-trigger
 * ``resetFamilyVault`` until they succeed (idempotent — no providers to
 * delete, no salt to clear). The zustand display slice is cleared in
 * ``finally`` so the chat screen re-derives from a clean state. */
export async function resetFamilyVault(): Promise<ResetResult> {
  // 1. Drop the server-side family provider row(s).
  let providersDeleted = 0;
  let serverSeedOk = true;
  try {
    const remote = await listFamilyProviders();
    await Promise.all(
      remote.map(async (p) => {
        await deleteFamilyProvider(p.id);
        providersDeleted += 1;
      }),
    );
  } catch {
    // Surface to the caller via providersDeleted < remote.length — the
    // user sees the error in the UI.
  }

  // 2. Clear the family_salt + family_enc_blob_seed on the families row
  //    so /family/vault/meta returns vault_initialized: false and the
  //    owner can re-mint a new family key.
  try {
    await clearFamilyVaultSeed();
  } catch {
    serverSeedOk = false;
  }

  // 3. Clear the in-memory zustand display pointer (always — even on partial
  //    failure the user is now in a "no family key" state on this device and
  //    the chat should reflect that).
  useStore.getState().setFamilyProvider(null);

  return { providersDeleted, serverSeedOk };
}
