import { useEffect, useRef } from 'react';
import L from 'leaflet';
import type { Place } from './places';
import { CATEGORIES } from './places';
import 'leaflet/dist/leaflet.css';
import './map.css';

const HILDESHEIM: L.LatLngTuple = [52.1508, 9.9511];

function pinIcon(place: Place, selected: boolean) {
  const color = CATEGORIES[place.category].color;
  const cls = ['kmap-pin', selected && 'is-selected', place.new && 'is-new'].filter(Boolean).join(' ');
  return L.divIcon({
    className: '',
    html: `<span class="${cls}" style="background:${color};color:${color}"></span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 22],
    tooltipAnchor: [0, -22],
  });
}

/**
 * Leaflet map with one pin per place (OpenStreetMap tiles). Pins are colored by category and
 * pulse when a hint is new. Selection is controlled by the parent, which shows the details.
 */
export default function PlaceMap({
  places,
  selected,
  onSelect,
  focus,
  wheelZoom = true,
  className,
}: {
  places: Place[];
  selected?: string | null;
  onSelect?: (key: string) => void;
  /** Keys to fit into view; defaults to all places. Changing it re-frames the map. */
  focus?: string[];
  /** Off in chat answers, where the wheel should scroll the conversation. */
  wheelZoom?: boolean;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;
  const frame = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (!box.current) return;
    const m = L.map(box.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: wheelZoom }).setView(
      HILDESHEIM,
      13,
    );
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
    }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    // The container often gets its final size after mount (chat layout, overlay animation).
    let sized = false;
    const resize = new ResizeObserver(() => {
      m.invalidateSize();
      if (!sized && m.getSize().x > 0 && m.getSize().y > 0) {
        sized = true;
        frame.current();
      }
    });
    resize.observe(box.current);
    return () => {
      resize.disconnect();
      m.remove();
      map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the map is created once
  }, []);

  useEffect(() => {
    const group = layer.current;
    if (!group) return;
    group.clearLayers();
    for (const p of places) {
      L.marker([p.lat, p.lon], {
        icon: pinIcon(p, p.key === selected),
        title: p.name,
        alt: p.name,
        keyboard: true,
        zIndexOffset: p.key === selected ? 1000 : 0,
      })
        .bindTooltip(p.name, { direction: 'top' })
        .on('click', () => select.current?.(p.key))
        .addTo(group);
    }
  }, [places, selected]);

  const focusKey = (focus ?? []).join('|');
  const placesKey = places.map((p) => p.key).join('|');
  useEffect(() => {
    frame.current = () => {
      const m = map.current;
      if (!m) return;
      const wanted = focus?.length ? places.filter((p) => focus.includes(p.key)) : places;
      if (!wanted.length) return;
      if (wanted.length === 1) {
        m.setView([wanted[0].lat, wanted[0].lon], 16);
        return;
      }
      m.fitBounds(L.latLngBounds(wanted.map((p) => [p.lat, p.lon] as L.LatLngTuple)), {
        padding: [36, 36],
        maxZoom: 16,
      });
    };
    // Framing needs the real size; before layout the ResizeObserver frames once it is known.
    if (map.current && map.current.getSize().x > 0) frame.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-frame only when the set changes
  }, [focusKey, placesKey]);

  // A place picked from a list: bring its pin into view without changing the zoom.
  useEffect(() => {
    const m = map.current;
    const p = places.find((x) => x.key === selected);
    if (!m || !p || m.getSize().x === 0) return;
    if (!m.getBounds().pad(-0.15).contains([p.lat, p.lon])) m.panTo([p.lat, p.lon]);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the selection changes
  }, [selected]);

  return <div ref={box} className={className ? `kmap ${className}` : 'kmap'} />;
}
