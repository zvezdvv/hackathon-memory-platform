import { createContext, useContext, useEffect, useMemo, ReactNode } from 'react';
import { atom, useAtom } from 'jotai';

export const DEFAULT_PANEL = 'conversations';
export const MEMORY_PANEL = 'memories';
export const MEMORY_PANE_ID = 'memory-pane';
export const MAP_PANEL = 'map';
/** Panels that open full-screen over the chat (in MEMORY_PANE_ID) instead of in the sidebar. */
export const OVERLAY_PANELS: ReadonlySet<string> = new Set([MEMORY_PANEL, MAP_PANEL]);
export const isOverlayPanel = (id: string) => OVERLAY_PANELS.has(id);

/**
 * Held in memory for the life of the document rather than in Web Storage, so every entry
 * (a new visit, a reload, a tab opened from the app or duplicated) starts on chat history,
 * while the sidebar remounting its provider at the mobile breakpoint keeps the selection.
 */
export const activePanelAtom = atom<string>(DEFAULT_PANEL);

interface ActivePanelContextType {
  active: string;
  setActive: (id: string) => void;
}

const ActivePanelContext = createContext<ActivePanelContextType | undefined>(undefined);

export function ActivePanelProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useAtom(activePanelAtom);

  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        setActive(DEFAULT_PANEL);
      }
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, [setActive]);

  const value = useMemo(() => ({ active, setActive }), [active, setActive]);

  return <ActivePanelContext.Provider value={value}>{children}</ActivePanelContext.Provider>;
}

export function useActivePanel() {
  const context = useContext(ActivePanelContext);
  if (context === undefined) {
    throw new Error('useActivePanel must be used within an ActivePanelProvider');
  }
  return context;
}

/** Returns `active` when it matches a known link, otherwise the first link's id. */
export function resolveActivePanel(active: string, links: { id: string }[]): string {
  if (links.length > 0 && links.some((l) => l.id === active)) {
    return active;
  }
  return links[0]?.id ?? active;
}
