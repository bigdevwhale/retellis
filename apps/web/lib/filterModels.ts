// Pure-logic model-id filter used by the ModelCombobox. Lives in its own
// module (no @/ aliases) so the unit test can import it without dragging
// the alias chain into vitest's resolver.

export function filterModels(models: string[], q: string): string[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return models;
  return models.filter((m) => {
    const ml = m.toLowerCase();
    if (ml.startsWith(needle)) return true;
    if (ml.includes(needle)) return true;
    const slash = ml.indexOf('/');
    if (slash > 0 && ml.slice(0, slash).startsWith(needle)) return true;
    return false;
  });
}
