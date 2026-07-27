import { describe, expect, it } from 'vitest';
import { CONVOS, EVENTS, PERSONAS, PLANS, personaById } from '../lib/fixtures';

describe('fixtures', () => {
  it('ships six built-in personas (aria, lou, mira, nico, sam, fam)', () => {
    expect(PERSONAS).toHaveLength(6);
    expect(PERSONAS.map((p) => p.id).sort()).toEqual(['aria', 'fam', 'lou', 'mira', 'nico', 'sam']);
  });

  it('personaById falls back to the first persona for unknown ids', () => {
    expect(personaById('aria').id).toBe('aria');
    expect(personaById('does-not-exist').id).toBe('aria');
  });

  it('every bilingual fixture has en + ru on all localized fields', () => {
    for (const p of PERSONAS) {
      for (const f of [p.role, p.vibe, p.open, p.prompt] as const) {
        expect(f.en.length).toBeGreaterThan(0);
        expect(f.ru.length).toBeGreaterThan(0);
      }
    }
    for (const e of EVENTS) {
      expect(e.level.en.length).toBeGreaterThan(0);
      expect(e.level.ru.length).toBeGreaterThan(0);
    }
  });

  it('convos reference existing personas', () => {
    for (const c of CONVOS) {
      expect(PERSONAS.some((p) => p.id === c.personaId)).toBe(true);
    }
  });

  it('plans keep the featured badge on Plus', () => {
    expect(PLANS.find((p) => p.id === 'plus')?.cls).toBe('featured');
  });
});
