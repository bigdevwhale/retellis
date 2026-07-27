'use client';

import { type ReactNode, createContext, useContext, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

type Ctx = { theme: Theme; setTheme: (t: Theme) => void; toggle: () => void };
const ThemeContext = createContext<Ctx | null>(null);

const STORAGE_KEY = 'companion.theme';

export function ThemeProvider({ children }: { children: ReactNode }) {
  // The pre-hydration boot script in ``app/layout.tsx`` has already set
  // ``data-theme`` to the saved value (or left the default 'dark'). We
  // read it back here so the first React render is consistent with the
  // markup the user actually saw — no dark→light flash, no useEffect
  // re-paint. Server-rendered initial state still defaults to 'dark'
  // (matching the static HTML) and is replaced on hydration if the boot
  // script set a different value.
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof document === 'undefined') return 'dark';
    const v = document.documentElement.getAttribute('data-theme');
    return v === 'light' ? 'light' : 'dark';
  });

  useEffect(() => {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', theme);
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', theme === 'dark' ? '#0d253d' : '#fbfaf7');
    }
    if (typeof window !== 'undefined') localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  return (
    <ThemeContext.Provider
      value={{
        theme,
        setTheme: setThemeState,
        toggle: () => setThemeState((p) => (p === 'dark' ? 'light' : 'dark')),
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): Ctx {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
