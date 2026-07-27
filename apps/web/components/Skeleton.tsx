// Reserved-height placeholder used by /family and /family/settings while
// the auth bootstrap and the family-slice hydration are still in flight.
// A skeleton with the same vertical rhythm as the real card means the
// swap from "loading" to "real content" is a paint, not a layout shift.
//
// Sizes are pre-sized to match the real cards so the eye doesn't have to
// re-track the page when the swap happens. A pulse animation gives the
// user a sense of liveness without being noisy.

import { useId } from 'react';

type SkeletonProps = {
  /** Width (CSS value) — defaults to 100%. */
  width?: string;
  /** Height (CSS value) — required so the parent reserves vertical space. */
  height: string;
  /** Optional class name for the wrapping div. */
  className?: string;
  /** Inline style for the wrapping div. */
  style?: React.CSSProperties;
};

export function Skeleton({ width = '100%', height, className, style }: SkeletonProps) {
  return (
    <div
      className={['skel', className].filter(Boolean).join(' ')}
      style={{ width, height, ...style }}
      aria-hidden="true"
    />
  );
}

/**
 * Card-shaped skeleton — matches a typical `.card` block on /family
 * (Members, therapy CTA). Reserves the height of the real content so
 * the page doesn't jump.
 */
export function SkeletonCard({
  lines = 3,
  style,
}: { lines?: number; style?: React.CSSProperties }) {
  const id = useId();
  return (
    <div className="card skel-card" style={style} aria-hidden="true">
      <Skeleton width="40%" height="18px" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={`${id}-${i}`}
          className="skel-line"
          height="12px"
          width={`${100 - i * 12}%`}
        />
      ))}
    </div>
  );
}

/**
 * Full /family primary-view skeleton: topbar + Members card + therapy
 * CTA card. Used by the primary page while auth + family hydration are
 * in flight.
 */
export function FamilyPrimarySkeleton() {
  return (
    <div className="wrap" aria-hidden="true">
      <div className="topbar" style={{ marginBottom: 16 }}>
        <Skeleton width="200px" height="22px" />
        <div style={{ height: 6 }} />
        <Skeleton width="320px" height="14px" />
      </div>
      <SkeletonCard lines={4} />
      <SkeletonCard lines={3} />
    </div>
  );
}

/**
 * /family/settings skeleton: tab strip + one card-shaped block (matches
 * the height of the active tab's content).
 */
export function FamilySettingsSkeleton() {
  return (
    <div className="wrap" aria-hidden="true">
      <div className="topbar" style={{ marginBottom: 16 }}>
        <Skeleton width="240px" height="22px" />
        <div style={{ height: 6 }} />
        <Skeleton width="280px" height="14px" />
      </div>
      <div className="skel-tab-strip">
        <Skeleton height="32px" />
        <Skeleton height="32px" />
        <Skeleton height="32px" />
        <Skeleton height="32px" />
        <Skeleton height="32px" />
      </div>
      <SkeletonCard lines={4} />
    </div>
  );
}
