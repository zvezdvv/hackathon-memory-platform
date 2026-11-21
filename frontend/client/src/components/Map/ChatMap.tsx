import { useEffect, useMemo, useState } from 'react';
import { useSetAtom } from 'jotai';
import { Loader2, Map as MapIcon, Maximize2 } from 'lucide-react';
import { activePanelAtom, MAP_PANEL } from '~/Providers/ActivePanelContext';
import { mapFocusAtom, usePlaces } from './places';
import PlaceCard from './PlaceCard';
import PlaceMap from './PlaceMap';

/** Waits until the streamed block content stops changing before it loads the map. */
function useSettled(value: string, ms = 500) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/**
 * Map preview inside a chat answer, rendered from a ```map block that lists place names
 * ("Steingrube; Bergstraße (Moritzberg)"). Coordinates and hints come from the knowledge base,
 * never from the model, so unknown names simply get no pin.
 */
export default function ChatMap({ content }: { content: string }) {
  const names = useSettled(content.replace(/\n/g, ';').trim());
  const { data, isLoading, isError } = usePlaces(names, names.length > 0);
  const [selected, setSelected] = useState<string | null>(null);
  const setFocus = useSetAtom(mapFocusAtom);
  const setPanel = useSetAtom(activePanelAtom);
  const places = useMemo(() => data?.places ?? [], [data]);
  const place = places.find((p) => p.key === selected);

  if (isError) return null;
  if (isLoading || !data) {
    return (
      <div className="my-3 flex h-24 items-center justify-center gap-2 whitespace-normal rounded-xl border border-border-light font-sans text-sm text-text-secondary">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading map …
      </div>
    );
  }
  if (!places.length) return null;

  return (
    // Rendered inside the code block's <pre>: reset its monospace font and no-wrap white-space.
    <div className="not-prose my-3 overflow-hidden whitespace-normal rounded-xl border border-border-light bg-surface-primary font-sans text-sm">
      <div className="flex items-center gap-2 border-b border-border-light px-3 py-1.5 text-xs text-text-secondary">
        <MapIcon className="size-3.5 text-accent-primary" aria-hidden="true" />
        <span className="flex-1 truncate">
          {places.length === 1 ? places[0].name : `${places.length} places`} · click a pin for details
        </span>
        <button
          type="button"
          onClick={() => {
            setFocus(names);
            setPanel(MAP_PANEL);
          }}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium text-text-primary hover:bg-surface-hover"
        >
          <Maximize2 className="size-3" aria-hidden="true" /> Open map
        </button>
      </div>
      <div className="relative">
        <PlaceMap places={places} selected={selected} onSelect={setSelected} wheelZoom={false} className="h-72 w-full" />
        {place && (
          <PlaceCard
            place={place}
            onClose={() => setSelected(null)}
            className="absolute inset-x-2 bottom-2 z-[1000] max-h-[60%] overflow-y-auto sm:inset-x-auto sm:right-2 sm:w-80"
          />
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        {places.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => setSelected(p.key === selected ? null : p.key)}
            className={
              p.key === selected
                ? 'rounded-full bg-surface-active-alt px-2 py-0.5 text-xs font-medium text-text-primary'
                : 'rounded-full border border-border-light px-2 py-0.5 text-xs text-text-secondary hover:bg-surface-hover'
            }
          >
            {p.name}
          </button>
        ))}
      </div>
    </div>
  );
}
