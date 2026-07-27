// Markdown helpers for the chat surface.
//
// ``stripMarkdown`` collapses common markdown markup to plain text for
// speech synthesis (TTS). The chat renderer (components/Markdown.tsx) turns
// ``**bold**`` into real <strong>; without this, ``speak()`` would hand the
// raw ``**`` to ``SpeechSynthesisUtterance`` and the user would hear
// "asterisk asterisk". This is a TTS-pitch cleaner, NOT a correct parser —
// it just needs to drop the symbols the ear shouldn't hear. Edge cases that
// mis-speak here are cosmetic; the visible bubble is always rendered by the
// real parser, so correctness for the eye is unaffected.

export function stripMarkdown(md: string): string {
  let s = md;
  // Fenced code blocks ```...``` → keep the inner text (it's often meant
  // to be read, e.g. a short snippet) but drop the fences.
  s = s.replace(/```[^\n]*\n?([\s\S]*?)```/g, '$1');
  // Inline code `x` → x
  s = s.replace(/`([^`]+)`/g, '$1');
  // Images ![alt](url) → alt
  s = s.replace(/!\[([^\]]*)\][^)]*\)/g, '$1');
  // Links [text](url) → text
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  // Bold **x** / __x__ → x
  s = s.replace(/\*\*([^*]+)\*\*/g, '$1');
  s = s.replace(/__([^_]+)__/g, '$1');
  // Strikethrough ~~x~~ → x
  s = s.replace(/~~([^~]+)~~/g, '$1');
  // Italic *x* / _x_ → x (single char markers, not part of a word like snake_case)
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1$2');
  s = s.replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, '$1$2');
  // ATX headings "# " / "## " → drop the leading hashes
  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '');
  // List markers "- " / "* " / "+ " / "1. " at line start → drop them
  s = s.replace(/^\s*[-*+]\s+/gm, '');
  s = s.replace(/^\s*\d+\.\s+/gm, '');
  // Task list markers "[x] " / "[ ] "
  s = s.replace(/^\s*\[[ xX]\]\s+/gm, '');
  // Blockquote "> " → drop
  s = s.replace(/^\s*>\s?/gm, '');
  // Horizontal rules "---" / "***" / "___" on their own line → drop
  s = s.replace(/^\s*([-*_/]\s?){3,}\s*$/gm, '');
  // Collapse 3+ newlines to 2 and trim
  s = s.replace(/\n{3,}/g, '\n\n').trim();
  return s;
}
