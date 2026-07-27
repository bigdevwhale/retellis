// I26: the chat page used to render ChatScreen optimistically while the auth
// bootstrap (/v1/auth/me) and the provider discovery were still in flight, so
// the "No key" banner flashed on every cold boot before the vault hydrated.
// ChatSkeleton is the same vertical rhythm as the real chat-screen — head,
// a stream of alternating bubbles, and a composer bar — so the swap from
// "loading" to "real content" is a paint, not a layout shift, and no
// misleading banner appears. Shown only while ``useAuthCtx().loading``.

import { Skeleton } from '@/components/Skeleton';

export function ChatSkeleton() {
  return (
    <div className="chat-screen chat-skeleton" aria-busy="true" aria-hidden="true">
      {/* Head: avatar + name + meta — same 42px avatar and row height as the
          real .chat-head so the boundary doesn't jump. */}
      <div className="chat-head">
        <Skeleton width="42px" height="42px" className="skel-av" />
        <div className="skel-head-text">
          <Skeleton width="120px" height="16px" />
          <div style={{ height: 6 }} />
          <Skeleton width="180px" height="11px" />
        </div>
      </div>
      {/* Stream: a few alternating them/me bubbles. Widths vary so the rhythm
          reads as a real conversation, not a stack of identical bars. */}
      <div className="stream">
        <Skeleton width="62%" height="44px" className="skel-bubble them" />
        <Skeleton width="48%" height="36px" className="skel-bubble me" />
        <Skeleton width="70%" height="52px" className="skel-bubble them" />
        <Skeleton width="40%" height="36px" className="skel-bubble me" />
        <Skeleton width="56%" height="44px" className="skel-bubble them" />
      </div>
      {/* Composer bar: a textarea-shaped block + a round send button. */}
      <div className="composer">
        <Skeleton width="100%" height="44px" className="skel-input" />
        <Skeleton width="44px" height="44px" className="skel-send" />
      </div>
    </div>
  );
}
