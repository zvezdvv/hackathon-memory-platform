import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

storage = importlib.import_module("app.store")
mapping = importlib.import_module("app.places")


class MapPlacesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        state = root / ".state"
        store_globals = patch.multiple(
            storage,
            ROOT=root,
            STATE=state,
            TEXT_CACHE=state / "text",
            AUDIO_DIR=state / "audio",
            INDEX_FILE=state / "index.json",
            STATUS_FILE=state / "document-status.json",
            VERSION_FILE=state / "index_version",
        )
        store_globals.start()
        self.addCleanup(store_globals.stop)
        self.store = storage.KnowledgeStore()
        self.store.ensure_layout()
        map_globals = patch.multiple(
            mapping,
            store=self.store,
            GEO_CACHE=state / "geocode.json",
            HINT_CACHE=state / "place-hints.json",
        )
        map_globals.start()
        self.addCleanup(map_globals.stop)
        no_llm = patch.object(mapping.ai, "enabled", return_value=False)
        no_llm.start()
        self.addCleanup(no_llm.stop)
        self.places = mapping.Places()

    def _file(self, relpath: str, text: str, status: str = "active") -> str:
        path = self.store.write_text(relpath, text, document_status=status)
        self.store.entries[path] = {"id": "kn_" + path, "status": "indexed", "mtime": 0, "size": 0}
        return path

    def test_spellings_of_one_place_share_a_pin_and_unknown_places_are_listed(self):
        self._file(
            "files/notizen/N03_bergstrasse.md",
            '---\ntyp: wissensnotiz\ntitel: "Busse bei Glatteis"\nort: "Bergstraße (Moritzberg)"\n---\n# Busse\n\nText',
        )
        self._file(
            "interviews/T01.md",
            '---\ntype: interview\ntitle: "Übergabe"\nplaces: ["Bergstraße Moritzberg", "Bauhof"]\n---\n# Übergabe\n\nText',
        )
        self._file(
            "files/notizen/N99_alt.md",
            '---\ntyp: wissensnotiz\ntitel: "Entwurf"\nort: "Steingrube"\n---\nText',
            status="draft",
        )
        with patch.object(self.places, "geocode_missing", return_value=None):
            data = asyncio.run(self.places.payload())
        self.assertEqual([p["name"] for p in data["places"]], ["Bergstraße (Moritzberg)"])
        hints = data["places"][0]["hints"]
        self.assertEqual({h["title"] for h in hints}, {"Busse bei Glatteis", "Übergabe"})
        self.assertEqual(data["unlocated"], ["Bauhof"])

    def test_names_filter_and_mappable_names_use_the_pin_name(self):
        path = self._file(
            "speech/2026-11-21_bruecke.md",
            '---\ntype: voice note\ntitle: "Brücke"\n'
            'places: ["Kennedydamm", "Innerste", "Innerste-Brücke", "St. Bernward", "St. Bernward Krankenhaus"]\n'
            '---\nText',
        )
        self.assertEqual(self.places.mappable(path), ["Kennedydamm", "St. Bernward Krankenhaus"])
        with patch.object(self.places, "geocode_missing", return_value=None):
            data = asyncio.run(self.places.payload("St. Bernward Krankenhaus"))
        self.assertEqual([p["key"] for p in data["places"]], ["st-bernward"])
        self.assertEqual(len(data["places"][0]["hints"]), 1)

    def test_fuzzy_geocoder_result_and_legacy_cache_do_not_create_pins(self):
        self._file("files/sample.md", '---\nort: "Treustraße"\n---\n# Sample')
        self.places.geo["treustrasse"] = {"name": "Treustraße", "lat": 52.15, "lon": 9.95}
        self.assertIsNone(self.places.resolve("Treustraße"))
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=[{"name": "Treibestraße", "category": "highway", "lat": "52.15", "lon": "9.95"}],
        )))
        with patch.object(mapping.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(self.places.payload())
        self.assertEqual(result["places"], [])
        self.assertEqual(result["unlocated"], ["Treustraße"])


if __name__ == "__main__":
    unittest.main()
