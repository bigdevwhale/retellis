// Pure-logic test for the model combobox filter — the risky part of the
// typeahead. Rendering is exercised manually (the component is small and
// pure-DOM); the helper is what determines whether typing "claude" or
// "haiku" or "openai" surfaces the rows the user expects.

import { describe, expect, it } from 'vitest';
import { filterModels } from '../lib/filterModels';

describe('filterModels', () => {
  const models = [
    'gpt-4o-mini',
    'gpt-4o',
    'gpt-4.1-mini',
    'gpt-4.1',
    'claude-3-5-haiku-latest',
    'claude-3-5-sonnet-latest',
    'gemini/gemini-1.5-flash',
    'gemini/gemini-2.0-flash',
    'openrouter/anthropic/claude-3.5-haiku',
  ];

  it('matches by prefix (also catches substrings at the start of "/"-prefixed ids)', () => {
    // "claude" is a prefix of "claude-3-5-*" AND a substring of
    // "openrouter/anthropic/claude-3.5-haiku" — both rules fire, so all
    // three rows match. The order is preserved from the input list.
    expect(filterModels(models, 'claude')).toEqual([
      'claude-3-5-haiku-latest',
      'claude-3-5-sonnet-latest',
      'openrouter/anthropic/claude-3.5-haiku',
    ]);
  });

  it('matches by substring anywhere in the id', () => {
    expect(filterModels(models, 'haiku')).toEqual([
      'claude-3-5-haiku-latest',
      'openrouter/anthropic/claude-3.5-haiku',
    ]);
  });

  it('matches by vendor prefix (the segment before the first "/")', () => {
    expect(filterModels(models, 'gemini')).toEqual([
      'gemini/gemini-1.5-flash',
      'gemini/gemini-2.0-flash',
    ]);
  });

  it('returns the full list on an empty query', () => {
    expect(filterModels(models, '')).toEqual(models);
  });

  it('is case-insensitive', () => {
    expect(filterModels(models, 'GPT')).toEqual([
      'gpt-4o-mini',
      'gpt-4o',
      'gpt-4.1-mini',
      'gpt-4.1',
    ]);
  });
});
