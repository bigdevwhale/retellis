'use client';

// Family accept screen — landed on from the email invite link. Reads the
// one-time token from the URL, drives the accept flow, and routes the user
// to the family settings screen. The "add a family key" prompt lives inside
// /family (Family key sub-tab), so there's no separate vault-setup route to
// dispatch to anymore.

import { useAuthCtx } from '@/lib/auth';
import { useStore } from '@/lib/store';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Suspense } from 'react';

function readInviteCookie(): string | null {
  // The accept page stashes the token in `family_invite_token` before
  // redirecting an unauthenticated user to /login. After local signup/login
  // the user returns to /family/accept with no `?token=` in the URL (the
  // `next` param doesn't carry it) — so the cookie is the only channel that
  // survives the auth redirect. Read it back here as a fallback.
  const raw = document.cookie
    .split(';')
    .map((s) => s.trim())
    .find((s) => s.startsWith('family_invite_token='));
  if (!raw) return null;
  const val = raw.slice('family_invite_token='.length);
  if (!val) return null;
  try {
    return decodeURIComponent(val);
  } catch {
    return val;
  }
}

function clearInviteCookie(): void {
  document.cookie = 'family_invite_token=; Path=/; Max-Age=0; SameSite=Lax';
}

function AcceptInner() {
  const params = useSearchParams();
  const router = useRouter();
  // ``loading`` lets us tell "auth bootstrap hasn't resolved" from "resolved
  // and there's no session". Without it, the first render fires this effect
  // with ``principal === null`` and redirects a signed-in user to /login
  // before the cookie is verified.
  const { principal, loading } = useAuthCtx();
  const loadFamily = useStore((s) => s.loadFamily);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // doAccept / router are intentionally captured on first mount: this effect
  // fires *because* principal changed (auth bootstrap), not because the body
  // reads them — exhaustive-deps is a false positive here.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset-on-change idiom
  useEffect(() => {
    if (loading) return; // wait for the auth bootstrap to finish
    // Prefer the URL token (fresh invite link); fall back to the cookie we
    // stashed before the auth redirect. Without the cookie fallback, a user
    // who signs up / logs in via the local flow lands here with no token and
    // sees "Missing invite token in the URL." — the magiclink auto-attach
    // path doesn't fire for local auth.
    const token = params.get('token') ?? readInviteCookie();
    if (!token) {
      setErr('Missing invite token in the URL.');
      return;
    }
    if (!principal) {
      // Stash the token in a short-lived cookie so the user can sign up / sign
      // in and the verify flow can pick it up. Per PLAN §Family accept: the
      // magiclink verify is the canonical auto-attach path; the explicit accept
      // endpoint is the second-chance / re-issue path used here.
      document.cookie = `family_invite_token=${encodeURIComponent(token)}; Path=/; Max-Age=1800; SameSite=Lax`;
      router.replace('/login?next=/family/accept');
      return;
    }
    // We have a principal + a token (URL or cookie). The cookie has served its
    // purpose — drop it so a later unauthenticated visit can't replay it.
    clearInviteCookie();
    void doAccept(token);
  }, [params, principal, loading]);

  // While the auth bootstrap is in flight, render nothing — the form would
  // flash for a moment and then redirect, which is jarring.
  if (loading && !principal) return null;

  const doAccept = async (token: string) => {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch('/v1/family/accept', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      if (!res.ok && res.status !== 303) {
        // Surface the server's detail so the user can see *why* the accept
        // failed (expired, replayed, wrong family, malformed seal). The page
        // also tries to read the response body for the structured detail.
        let detail = `HTTP ${res.status}`;
        try {
          const body = (await res.json()) as { detail?: string };
          if (body?.detail) detail = `${res.status} — ${body.detail}`;
        } catch {
          /* body wasn't JSON — keep the status-only message */
        }
        throw new Error(detail);
      }
      // Hydrate the family slice, then route to /family — the Family key
      // sub-tab there shows the "add a family key" flow if no key is set.
      // We don't have a user_id handy here — loadFamily() tolerates 404 and
      // we read my user from /v1/auth/me indirectly via the principal.
      await loadFamily(principal!.user_id);
      router.replace('/family');
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wrap fam-wrap">
      <div
        className="card fam-narrow"
        style={{ margin: '40px auto', borderColor: err ? 'var(--danger)' : undefined }}
      >
        <div className="card-title">Joining the family…</div>
        <div className="help" style={{ marginBottom: 12 }}>
          {busy ? (
            <>
              <span className="fam-spin" aria-hidden="true" />
              Verifying your invite…
            </>
          ) : (
            (err ??
            'If this takes more than a few seconds, return to the email and try the link again.')
          )}
        </div>
        {err && (
          <div className="fam-actions" style={{ marginTop: 0 }}>
            <button type="button" className="btn" onClick={() => router.replace('/')}>
              Back to home
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function FamilyAcceptPage() {
  return (
    <Suspense fallback={null}>
      <AcceptInner />
    </Suspense>
  );
}
