import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

storage = importlib.import_module("app.store")


class KnowledgeOrganizeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        state = root / ".state"
        globals = patch.multiple(
            storage,
            ROOT=root,
            STATE=state,
            TEXT_CACHE=state / "text",
            AUDIO_DIR=state / "audio",
            INDEX_FILE=state / "index.json",
            STATUS_FILE=state / "document-status.json",
            VERSION_FILE=state / "index_version",
        )
        globals.start()
        self.addCleanup(globals.stop)
        self.root = root
        self.store = storage.KnowledgeStore()
        self.store.ensure_layout()

    def _file(self, relpath: str, text: str = "Inhalt", status: str = "active") -> str:
        path = self.store.write_text(relpath, text, document_status=status)
        self.store.entries[path] = {"id": "kn_test_" + path, "status": "indexed", "mtime": 0, "size": 0}
        return path

    def test_interviews_are_saved_flat_with_topic_and_person_in_the_name(self):
        path = self.store.save_interview("Klaus Behrens", "Glätte nachts", "# Protokoll\n\nText")
        self.assertRegex(path, r"^interviews/\d{4}-\d{2}-\d{2}_glaette-nachts_klaus-behrens\.md$")
        self.store.entries[path] = {"id": "kn_i", "status": "indexed", "mtime": 0, "size": 0}
        people = self.store.people()
        self.assertEqual([p["name"] for p in people], ["Klaus Behrens"])
        self.assertEqual(people[0]["sessions"], 1)

    def test_voice_notes_are_saved_flat_with_topic_as_metadata(self):
        path = self.store.save_voice_note("Text", {"title": "Brücken zuerst", "topic": "Winterdienst"})
        self.assertRegex(path, r"^speech/\d{4}-\d{2}-\d{2}_bruecken-zuerst\.md$")
        self.assertIn('topic: "Winterdienst"', (self.root / path).read_text("utf-8"))

    def test_next_interview_session_follows_the_three_session_plan(self):
        self.assertEqual(self.store.next_session("Klaus Behrens"), (1, "_guides/01_workday-walkthrough.md"))
        for n in (1, 2, 3):
            status = "draft" if n == 1 else "active"  # drafts count as completed sessions too
            path = self.store.save_interview("Klaus Behrens", f"Thema {n}", "# P", session=n, document_status=status)
            self.store.entries[path] = {"id": f"kn_{n}", "status": "indexed", "mtime": 0, "size": 0}
            expected = {1: "02_exceptions", 2: "03_plan-vs-reality", 3: "04_deep-dive"}[n]
            number, guide = self.store.next_session("Klaus Behrens")
            self.assertEqual(number, n + 1)
            self.assertIn(expected, guide)
        # handover talks without a session number do not advance the plan for others
        self.assertEqual(self.store.next_session("Miriam Seidel")[0], 1)

    def test_previous_open_points_are_listed_for_the_next_session(self):
        body = "# P\n\n## Kurzfassung\n- a\n\n## Offene Punkte (für die nächste Session)\n- Wer ruft den Busbetrieb an?\n- Ab welcher Temperatur?\n"
        path = self.store.save_interview("Klaus Behrens", "Glätte", body, session=1)
        self.store.entries[path] = {"id": "kn_o", "status": "indexed", "mtime": 0, "size": 0}
        sessions = self.store.interview_sessions("Klaus Behrens")
        self.assertEqual(sessions[0]["open_points"], ["Wer ruft den Busbetrieb an?", "Ab welcher Temperatur?"])


if __name__ == "__main__":
    unittest.main()
