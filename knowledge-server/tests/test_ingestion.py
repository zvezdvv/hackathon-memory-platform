import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import pdf

storage = importlib.import_module("app.store")


class KnowledgeIngestionTests(unittest.IsolatedAsyncioTestCase):
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
            INDEX_FILE=state / "index.json",
            STATUS_FILE=state / "document-status.json",
            VERSION_FILE=state / "index_version",
        )
        globals.start()
        self.addCleanup(globals.stop)
        self.store = storage.KnowledgeStore()
        self.store.ensure_layout()

    async def test_excludes_draft_and_inactive_from_both_search_paths(self):
        active = self.store.save_upload("files", "active.txt", b"Winterdienst code ACTIVE")
        draft = self.store.save_upload("files", "draft.md", b"Winterdienst code DRAFT", "draft")
        inactive = self.store.save_upload("files", "inactive.txt", b"Winterdienst code INACTIVE", "inactive")
        for entry in self.store.entries.values():
            entry["status"] = "indexed"
        self.store._save()

        restarted = storage.KnowledgeStore()
        restarted.ensure_layout()
        restarted._rag = AsyncMock(return_value=httpx.Response(200, json=[
            [{"metadata": {"file_id": storage.file_id_for(path)}, "page_content": f"Winterdienst {path}"}, 0.1]
            for path in (active, draft, inactive)
        ]))
        result = await restarted.search("Winterdienst")
        self.assertEqual(restarted._rag.call_args.kwargs["json"]["file_ids"], [storage.file_id_for(active)])
        self.assertEqual({hit["path"] for hit in result["results"]}, {active})
        self.assertEqual(restarted.document_status(draft), "draft")

        restarted.set_status(draft, "active")
        restarted.set_status(active, "inactive")
        result = await restarted.search("Winterdienst")
        self.assertEqual({hit["path"] for hit in result["results"]}, {draft})

    async def test_legacy_pdf_is_reprocessed_and_only_transcript_is_embedded(self):
        original = b"%PDF-1.7 original contents"
        path = self.store.save_upload("files", "sample.pdf", original)
        self.store.entries[path]["status"] = "indexed"
        self.assertEqual(self.store.get_text(path), "")
        request = httpx.Request("POST", "http://rag.test/embed")
        self.store._rag = AsyncMock(side_effect=[
            httpx.Response(200, request=request),
            httpx.Response(200, json={"status": True}, request=request),
        ])
        with patch.object(storage, "extract_pdf", AsyncMock(return_value="Recognizable section § 3")) as extract:
            await self.store.sync()
        extract.assert_awaited_once_with("sample.pdf", original)
        self.assertEqual(self.store._rag.await_args_list[0].args, ("DELETE", "/documents"))
        self.assertEqual(
            self.store._rag.await_args.kwargs["files"]["file"],
            ("sample.txt", "Recognizable section § 3".encode()),
        )
        self.assertEqual(self.store.get_text(path), "Recognizable section § 3")
        self.assertEqual(self.store.index_status(path, self.store.entries[path]), "indexed")

    async def test_pdf_extraction_error_cannot_fall_back_to_old_text(self):
        path = self.store.save_upload("files", "sample.pdf", b"%PDF-1.7 original contents")
        self.store.entries[path]["status"] = "indexed"
        cache = storage.TEXT_CACHE / f"{storage.file_id_for(path)}.txt"
        cache.write_text("corrupt legacy text")
        self.store._rag = AsyncMock()
        with patch.object(storage, "extract_pdf", AsyncMock(side_effect=RuntimeError("read failed"))):
            await self.store.sync()
        self.assertEqual(self.store.get_text(path), "")
        self.assertEqual(self.store.index_status(path, self.store.entries[path]), "error")
        self.store._rag.assert_not_awaited()

    def test_unsupported_uploads_are_refused(self):
        with self.assertRaisesRegex(ValueError, "PDF, Markdown and TXT"):
            self.store.save_upload("files", "sample.csv", b"foo,bar")
        with self.assertRaisesRegex(ValueError, "valid PDF"):
            self.store.save_upload("files", "sample.pdf", b"not a pdf")


class NativePdfTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_pdf_as_native_file_and_refuses_truncated_output(self):
        params = {}

        def respond(request):
            params.update(json.loads(request.content))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "partial text"}, "finish_reason": "length"}],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.dict("os.environ", {"OPENROUTER_KEY": "test-key"}):
            with patch.object(pdf.httpx, "AsyncClient", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "output limit"):
                    await pdf.extract_pdf("input.pdf", b"%PDF")
        self.assertEqual(params["model"], "openai/gpt-6.1-terra")
        self.assertEqual(params["plugins"][0]["pdf"]["engine"], "native")
        self.assertEqual(params["messages"][1]["content"][1]["file"], {
            "file_data": "data:application/pdf;base64,JVBERg==",
            "filename": "input.pdf",
        })
