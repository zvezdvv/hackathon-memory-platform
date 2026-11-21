/**
 * Smart City map: pins for places named in the knowledge base (knowledge server /api/places).
 * Shared by the Map tab, the map preview in chat answers and "Show on map" in Memory.
 */
import { atom } from 'jotai';
import { useQuery } from '@tanstack/react-query';
import { KB_URL } from '~/components/SidePanel/Knowledge/kb';

export type Category = 'danger' | 'traffic' | 'priority' | 'info';

export type Hint = {
  text: string;
  category: Category;
  doc_id: string;
  title: string;
  path: string;
  url: string;
  typ: string;
  person: string;
  date: string;
  outdated: boolean;
  new: boolean;
};

export type Place = {
  key: string;
  name: string;
  lat: number;
  lon: number;
  category: Category;
  new: boolean;
  hints: Hint[];
};

type PlacesResponse = { city: string; places: Place[]; unlocated: string[]; pending: number };

export const CATEGORIES: Record<Category, { label: string; color: string }> = {
  danger: { label: 'Ice & hazards', color: '#DC2626' },
  traffic: { label: 'Traffic & parking', color: '#2563EB' },
  priority: { label: 'Priority', color: '#F59E0B' },
  info: { label: 'Info', color: '#14B8A6' },
};

/** Place names to focus when the Map tab opens ("A; B"), set from chat or Memory. */
export const mapFocusAtom = atom<string>('');

export function viewerUrl(path: string) {
  return `${KB_URL}/view?path=${encodeURIComponent(path)}`;
}

/** All pins, or only those for `names` ("A; B") when given. */
export function usePlaces(names?: string, enabled = true) {
  const filter = names?.trim() ?? '';
  return useQuery<PlacesResponse>({
    queryKey: ['knowledge-places', filter],
    enabled,
    staleTime: 30_000,
    // Hints of new documents are extracted in the background: ask again while some are pending.
    refetchInterval: (data) => ((data?.pending ?? 0) > 0 ? 4000 : false),
    queryFn: async () => {
      const qs = filter ? `?names=${encodeURIComponent(filter)}` : '';
      const res = await fetch(`${KB_URL}/api/places${qs}`);
      if (!res.ok) throw new Error(`Map data unavailable (HTTP ${res.status})`);
      return res.json();
    },
  });
}
