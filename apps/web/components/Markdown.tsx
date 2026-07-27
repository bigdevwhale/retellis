'use client';

// Markdown renderer for assistant chat bubbles. ``react-markdown`` does NOT
// render raw HTML by default (we deliberately do not add ``rehype-raw``), so
// arbitrary LLM output cannot inject markup — this preserves the project's
// "don't claim guarantees you can't back" stance. ``remark-gfm`` adds tables,
// strikethrough, autolinks, and task lists. Links open in a new tab with
// ``noopener`` so the LLM's URLs can't reach back into ``window.opener``.

import type { AnchorHTMLAttributes, ImgHTMLAttributes, TableHTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
          const { href, children: linkChildren, ...rest } = props;
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
              {linkChildren}
            </a>
          );
        },
        // Force images responsive + lazy so an LLM `![](url)` can't overflow the
        // chat bubble or trigger page-level horizontal scroll on mobile.
        img(props: ImgHTMLAttributes<HTMLImageElement>) {
          const { src, alt = '', ...rest } = props;
          // biome-ignore lint/a11y/useAltText: alt is destructured above with a default; react-markdown always passes the author's alt text (empty for decorative), which static analysis can't trace through the variable.
          return <img src={src} alt={alt} loading="lazy" decoding="async" {...rest} />;
        },
        // Wrap GFM tables in a horizontal-scroll container so wide tables scroll
        // in place instead of stretching the message bubble / the whole stream.
        table(props: TableHTMLAttributes<HTMLTableElement>) {
          const { children, ...rest } = props;
          return (
            <div className="md-table-wrap">
              <table {...rest}>{children}</table>
            </div>
          );
        },
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
