import { MapPin, X } from 'lucide-react';
import type { Hint, Place } from './places';
import { CATEGORIES } from './places';
import { cn } from '~/utils';

function Dot({ color }: { color: string }) {
  return <span className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden="true" />;
}

/** What to watch out for at one place: one line per source document, newest first. */
export default function PlaceCard({
  place,
  onClose,
  onOpenSource,
  className,
}: {
  place: Place;
  onClose?: () => void;
  /** Opens a source; without it sources open in a new tab. */
  onOpenSource?: (hint: Hint) => void;
  className?: string;
}) {
  const cat = CATEGORIES[place.category];
  return (
    <div className={cn('rounded-xl border border-border-light bg-surface-primary p-3 shadow-lg', className)}>
      <div className="mb-2 flex items-start gap-2">
        <MapPin className="mt-0.5 size-4 shrink-0" style={{ color: cat.color }} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-text-primary">{place.name}</div>
          <div className="text-xs" style={{ color: cat.color }}>{cat.label}</div>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-text-secondary hover:bg-surface-hover"
            aria-label="Close"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
      <ul className="flex flex-col gap-2">
        {place.hints.map((h) => (
          <li key={h.path} className={cn('flex gap-2 text-sm', h.outdated && 'opacity-55')}>
            <Dot color={CATEGORIES[h.category].color} />
            <div className="min-w-0 flex-1">
              <div className="text-text-primary">
                {h.text}
                {h.new && (
                  <span className="ms-1.5 rounded-full bg-amber-500/15 px-1.5 text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-300">
                    New
                  </span>
                )}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-text-secondary">
                <a
                  href={h.url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => {
                    if (!onOpenSource) return;
                    e.preventDefault();
                    onOpenSource(h);
                  }}
                  className="font-medium text-accent-primary hover:underline"
                  title={h.title}
                >
                  {h.doc_id || h.title}
                </a>
                <span>· {h.typ}</span>
                {h.person && <span>· {h.person}</span>}
                <span>· {h.date}</span>
                {h.outdated && <span className="italic">· possibly outdated</span>}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
