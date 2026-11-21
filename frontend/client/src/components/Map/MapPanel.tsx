/**
 * Smart City: "Map" tab. Every place named in the knowledge base as a pin with short hints on
 * what to watch out for there, so a driver sees the route's pitfalls without reading the notes.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAtom } from 'jotai';
import { ExternalLink, Loader2, MapPin, Search, X } from 'lucide-react';
import type { Category, Hint } from './places';
import { DEFAULT_PANEL, MEMORY_PANE_ID, useActivePanel } from '~/Providers';
import OpenSidebar from '~/components/Chat/Menus/OpenSidebar';
import useSidebarState from '~/hooks/Nav/useSidebarState';
import { CATEGORIES, mapFocusAtom, usePlaces, viewerUrl } from './places';
import PlaceCard from './PlaceCard';
import PlaceMap from './PlaceMap';
import { cn } from '~/utils';

function SourceViewer({ hint, onClose }: { hint: Hint; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const src = viewerUrl(hint.path);
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={hint.title}
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-2 sm:p-6"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex h-full w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-border-medium bg-surface-primary shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border-light px-4 py-2.5">
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-text-primary">{hint.title}</h2>
          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            className="rounded-md p-1 text-text-secondary hover:bg-surface-hover"
            title="Open in new tab"
          >
            <ExternalLink className="size-4" />
          </a>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-text-secondary hover:bg-surface-hover"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>
        <iframe title={hint.title} src={`${src}&embed=1`} className="size-full border-0 bg-surface-primary" />
      </div>
    </div>,
    document.body,
  );
}

export default function MapPanel() {
  const { isSmallScreen } = useSidebarState();
  const { setActive } = useActivePanel();
  const [focusNames, setFocusNames] = useAtom(mapFocusAtom);
  const { data, isLoading, isError } = usePlaces();
  const focused = usePlaces(focusNames, focusNames.length > 0);
  const [selected, setSelected] = useState<string | null>(null);
  const [categories, setCategories] = useState<Set<Category>>(new Set());
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<Hint | null>(null);

  const all = useMemo(() => data?.places ?? [], [data]);
  const focus = useMemo(() => (focused.data?.places ?? []).map((p) => p.key), [focused.data]);
  // Coming from chat or Memory: select the first named place once its key is known.
  useEffect(() => {
    if (focus.length) setSelected(focus[0]);
  }, [focus]);

  const places = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter(
      (p) =>
        (!categories.size || categories.has(p.category)) &&
        (!q || p.name.toLowerCase().includes(q) || p.hints.some((h) => h.text.toLowerCase().includes(q))),
    );
  }, [all, categories, query]);
  const place = all.find((p) => p.key === selected);
  const hintCount = all.reduce((n, p) => n + p.hints.length, 0);

  const toggle = useCallback((c: Category) => {
    setCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  }, []);

  const pane = document.getElementById(MEMORY_PANE_ID);
  if (!pane) return null;

  return createPortal(
    <div role="region" aria-labelledby="map-overlay-title" className="relative flex size-full flex-col bg-surface-primary">
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-light px-4 py-2.5">
        {isSmallScreen && <OpenSidebar testId="map-open-sidebar" />}
        <img src="assets/stadtgedaechtnis.svg" className="size-9 shrink-0" alt="" />
        <div className="mr-2">
          <h1 id="map-overlay-title" className="text-base font-semibold text-text-primary">
            Map <span className="font-normal text-text-secondary">· Stadtgedächtnis</span>
          </h1>
          <p className="text-xs text-text-secondary">
            {data ? `${all.length} places · ${hintCount} hints from the knowledge base` : 'loading …'}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(Object.keys(CATEGORIES) as Category[]).map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => toggle(c)}
              aria-pressed={categories.has(c)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
                categories.has(c)
                  ? 'border-border-heavy bg-surface-active-alt font-medium text-text-primary'
                  : 'border-border-light text-text-secondary hover:bg-surface-hover',
              )}
            >
              <span className="size-2 rounded-full" style={{ background: CATEGORIES[c].color }} />
              {CATEGORIES[c].label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => {
            setFocusNames('');
            setActive(DEFAULT_PANEL);
          }}
          className="rounded-md p-1.5 text-text-secondary hover:bg-surface-hover"
          aria-label="Close map"
          title="Close map"
        >
          <X className="size-5" />
        </button>
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="flex max-h-[40%] shrink-0 flex-col border-b border-border-light md:max-h-none md:w-80 md:border-b-0 md:border-e">
          <div className="relative p-3 pb-2">
            <Search className="pointer-events-none absolute left-5 top-5.5 size-4 text-text-tertiary" />
            <input
              className="w-full rounded-lg border border-border-medium bg-surface-secondary py-2 pl-8 pr-3 text-sm text-text-primary placeholder:text-text-tertiary focus:border-border-heavy focus:outline-none"
              placeholder="Search places and hints …"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {places.map((p) => (
              <li key={p.key}>
                <button
                  type="button"
                  onClick={() => setSelected(p.key)}
                  className={cn(
                    'flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-surface-hover',
                    p.key === selected && 'bg-surface-active-alt',
                  )}
                >
                  <MapPin className="mt-0.5 size-4 shrink-0" style={{ color: CATEGORIES[p.category].color }} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                      <span className="truncate">{p.name}</span>
                      {p.new && (
                        <span className="rounded-full bg-amber-500/15 px-1.5 text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-300">
                          New
                        </span>
                      )}
                    </span>
                    <span className="line-clamp-2 text-xs text-text-secondary">{p.hints[0]?.text}</span>
                  </span>
                  <span className="text-xs text-text-tertiary">{p.hints.length}</span>
                </button>
              </li>
            ))}
            {data && !places.length && (
              <li className="px-2 py-6 text-center text-sm text-text-secondary">No places match.</li>
            )}
          </ul>
          {!!data?.unlocated.length && (
            <p className="border-t border-border-light px-4 py-2 text-xs text-text-tertiary">
              Without map position: {data.unlocated.join(', ')}
            </p>
          )}
        </aside>

        <div className="relative min-h-0 flex-1">
          {isLoading && (
            <div className="absolute inset-0 z-[1001] flex items-center justify-center gap-2 text-sm text-text-secondary">
              <Loader2 className="size-4 animate-spin" /> Loading map …
            </div>
          )}
          {isError && (
            <div className="absolute inset-0 z-[1001] flex items-center justify-center text-sm text-text-secondary">
              Map data is unavailable right now.
            </div>
          )}
          <PlaceMap
            places={places}
            selected={selected}
            onSelect={setSelected}
            focus={focus.length ? focus : undefined}
            className="size-full"
          />
          {place && (
            <PlaceCard
              place={place}
              onClose={() => setSelected(null)}
              onOpenSource={setSource}
              className="absolute inset-x-3 bottom-3 z-[1000] max-h-[55%] overflow-y-auto md:inset-x-auto md:bottom-auto md:right-3 md:top-3 md:max-h-[calc(100%-1.5rem)] md:w-96"
            />
          )}
        </div>
      </div>
      {source && <SourceViewer hint={source} onClose={() => setSource(null)} />}
    </div>,
    pane,
  );
}
