import importlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, Mock, patch

import httpx

from app import ai, pdf

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
            AUDIO_DIR=state / "audio",
            INDEX_FILE=state / "index.json",
            STATUS_FILE=state / "document-status.json",
            VERSION_FILE=state / "index_version",
        )
        globals.start()
        self.addCleanup(globals.stop)
        self.store = storage.KnowledgeStore()
        self.store.ensure_layout()

    async def test_excludes_draft_and_inactive_from_both_search_paths(self):
        active, draft, inactive = await self.store.save_uploads("files", [
            ("active.txt", b"Winterdienst code ACTIVE", "active"),
            ("draft.md", b"Winterdienst code DRAFT", "draft"),
            ("inactive.txt", b"Winterdienst code INACTIVE", "inactive"),
        ])
        for entry in self.store.entries.values():
            entry["status"] = "indexed"
            entry["search_text_version"] = storage.SEARCH_TEXT_VERSION
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

    async def test_search_filters_both_retrieval_paths_before_ranking(self):
        first, second, law = await self.store.save_uploads("files", [
            ("first.md", b"---\ntyp: note\nautor: Lina (Dispatcher)\n---\n\nRoad salt container.", "active"),
            ("second.md", b"---\ntyp: note\nautor: Max\n---\n\nRoad salt order.", "active"),
            ("law.md", b"---\ngesetz: Winter rules\nautor: Lina\n---\n\nRoad salt rules.", "active"),
        ])
        for entry in self.store.entries.values():
            entry.update(status="indexed", search_text_version=storage.SEARCH_TEXT_VERSION)
        self.store._rag = AsyncMock(return_value=httpx.Response(200, json=[
            [{"metadata": {"file_id": storage.file_id_for(path)}, "page_content": "Road salt"}, 0.25]
            for path in (second, law, first)
        ]))
        response = await self.store.search("road salt", "files", 1, typ="Note", person="Lina")
        self.assertEqual([hit["path"] for hit in response["results"]], [first])
        self.assertEqual(self.store._rag.call_args.kwargs["json"]["file_ids"], [storage.file_id_for(first)])
        self.store._rag.return_value = httpx.Response(503)
        response = await self.store.search("road salt", typ="Law", person="Lina")
        self.assertEqual([hit["path"] for hit in response["results"]], [law])

    async def test_search_keeps_semantic_and_exact_matches_but_rejects_weak_candidates(self):
        [path] = await self.store.save_uploads("files", [("salt.txt", b"Streusalz: ID ZX730.", "active")])
        self.store.entries[path].update(status="indexed", search_text_version=storage.SEARCH_TEXT_VERSION)
        chunk = {"metadata": {"file_id": storage.file_id_for(path)}, "page_content": "Streusalz: ID ZX730."}
        self.store._rag = AsyncMock(return_value=httpx.Response(200, json=[[chunk, 0.74]]))
        self.assertEqual((await self.store.search("zzqaunmatchable792orvb"))["results"], [])
        self.assertEqual((await self.store.search("ZX730"))["mode"], "keyword")
        self.store._rag.return_value = httpx.Response(200, json=[[chunk, 0.61]])
        self.assertEqual((await self.store.search("Streusalz Cookie-Richtlinie"))["results"], [])
        self.store._rag.return_value = httpx.Response(200, json=[[chunk, 0.52]])
        self.assertEqual((await self.store.search("deicing supply"))["results"][0]["path"], path)

    async def test_natural_language_search_finds_exact_documented_place(self):
        active, _ = await self.store.save_uploads("files", [
            (
                "bridge.md",
                b'---\nplaces: ["Kennedydamm", "Innerste"]\n---\n'
                b"# Bridge note\n\nCheck the Kennedydamm-Bruecke for ice.\n\nAn unrelated paragraph.",
                "active",
            ),
            ("draft.md", b'---\nort: "Kennedydamm"\n---\nKennedydamm draft.', "draft"),
        ])
        query = "what should i know about Kennedydamm?"
        results = (await self.store.search(query))["results"]
        self.assertEqual([hit["path"] for hit in results], [active])
        self.assertIn("Kennedydamm-Bruecke", results[0]["snippet"])
        self.assertEqual((await self.store.search(query.replace("Kennedydamm", "Kennedy")))["results"], [])

    async def test_mcp_search_reranks_twelve_candidates_and_returns_six(self):
        from app import mcp_tools

        paths = await self.store.save_uploads("files", [
            (f"source-{index}.txt", b"Document text.", "active") for index in range(13)
        ])
        passages = [f"Passage {index}: " + "chunk text " * 110 for index in range(13)]
        for entry in self.store.entries.values():
            entry.update(status="indexed", search_text_version=storage.SEARCH_TEXT_VERSION)
        self.store._rag = AsyncMock(return_value=httpx.Response(200, json=[
            [
                {"metadata": {"file_id": storage.file_id_for(path)}, "page_content": passage},
                0.3,
            ]
            for path, passage in zip(paths, passages)
        ]))

        ranking = [11, 7, 3, 9, 0, 1]

        def respond(request):
            self.assertEqual(str(request.url), "https://openrouter.ai/api/v1/rerank")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "cohere/rerank-4-fast")
            self.assertEqual(payload["query"], "winter salt")
            self.assertEqual(payload["documents"], [passage.strip() for passage in passages[:12]])
            self.assertEqual(payload["top_n"], 6)
            return httpx.Response(200, json={"results": [{"index": i} for i in ranking]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with (
            patch.object(mcp_tools, "store", self.store),
            patch.object(ai, "OPENROUTER_KEY", "test-key"),
            patch.object(ai.httpx, "AsyncClient", return_value=client),
        ):
            response = await mcp_tools.search_knowledge("winter salt", max_results=12)
        self.assertIn("6 results", response)
        sources = [line for line in response.splitlines() if "Source (" in line]
        self.assertEqual(len(sources), len(ranking))
        for line, index in zip(sources, ranking):
            self.assertIn(f"[{paths[index]}](", line)

    async def test_reranker_failure_keeps_the_first_results(self):
        response = httpx.Response(503)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
        with (
            patch.object(ai, "OPENROUTER_KEY", "test-key"),
            patch.object(ai.httpx, "AsyncClient", return_value=client),
            self.assertLogs("knowledge", "WARNING"),
        ):
            self.assertEqual(await ai.rerank("winter salt", ["first", "second", "third"], 2), [0, 1])

    async def test_reindexes_clean_search_text_and_preserves_source_metadata(self):
        source = (
            "---\ntitle: Street Act\nautor: Lina\n---\n\n# First section\n\nOriginal rule.\n"
            " -->\n Feedback\n-->\n Verwendung von Cookies.\n"
            " Cookie-Richtlinie\n Akzeptieren\n Nachricht senden\n\n"
            "---\n\n# Second section\n\nOrtsdurchfahrt.\n"
        )
        [path] = await self.store.save_uploads("files", [("law.md", source.encode(), "active")])
        self.store.entries[path]["status"] = "indexed"
        self.assertEqual(self.store.index_status(path, self.store.entries[path]), "pending")
        self.store._rag = AsyncMock(return_value=httpx.Response(
            200, json={"status": True}, request=httpx.Request("POST", "http://rag.test"),
        ))
        await self.store.sync()
        self.assertEqual(self.store._rag.await_args_list[0].args, ("DELETE", "/documents"))
        filename, indexed = self.store._rag.await_args.kwargs["files"]["file"]
        self.assertEqual(filename, "law.txt")
        self.assertIn(b"Original rule.", indexed)
        self.assertIn(b"Ortsdurchfahrt.", indexed)
        self.assertNotIn(b"Cookies", indexed)
        self.assertNotIn(b"autor:", indexed)
        self.assertEqual(self.store.get_text(path), source)
        self.assertEqual(self.store.documents()[0]["title"], "Street Act")
        self.assertEqual(self.store.index_status(path, self.store.entries[path]), "indexed")
        self.store._rag.return_value = httpx.Response(404)
        self.assertEqual((await self.store.search("Cookies"))["results"], [])
        self.assertEqual((await self.store.search("Ortsdurchfahrt"))["results"][0]["path"], path)

    async def test_legacy_pdf_is_reprocessed_and_only_transcript_is_embedded(self):
        original = b"%PDF-1.7 original contents"
        with patch.object(storage, "pdf_page_count", return_value=1):
            [path] = await self.store.save_uploads("files", [("sample.pdf", original, "active")])
        self.store.entries[path]["status"] = "indexed"
        self.assertEqual(self.store.get_text(path), "")
        request = httpx.Request("POST", "http://rag.test/embed")
        self.store._rag = AsyncMock(side_effect=[
            httpx.Response(200, request=request),
            httpx.Response(200, json={"status": True}, request=request),
        ])
        with patch.object(storage, "extract_pdf", AsyncMock(return_value="Recognizable section § 3")) as extract:
            await self.store.sync()
        extract.assert_awaited_once_with(original, on_progress=ANY)
        self.assertEqual(self.store._rag.await_args_list[0].args, ("DELETE", "/documents"))
        self.assertEqual(
            self.store._rag.await_args.kwargs["files"]["file"],
            ("sample.txt", "Recognizable section § 3".encode()),
        )
        self.assertEqual(self.store.get_text(path), "Recognizable section § 3")
        self.assertEqual(self.store.index_status(path, self.store.entries[path]), "indexed")

    async def test_pdf_extraction_error_cannot_fall_back_to_old_text(self):
        with patch.object(storage, "pdf_page_count", return_value=1):
            [path] = await self.store.save_uploads(
                "files", [("sample.pdf", b"%PDF-1.7 original contents", "active")],
            )
        self.store.entries[path]["status"] = "indexed"
        cache = storage.TEXT_CACHE / f"{storage.file_id_for(path)}.txt"
        cache.write_text("corrupt legacy text")
        self.store._rag = AsyncMock()
        with patch.object(storage, "extract_pdf", AsyncMock(side_effect=RuntimeError("read failed"))):
            await self.store.sync()
        self.assertEqual(self.store.get_text(path), "")
        self.assertEqual(self.store.index_status(path, self.store.entries[path]), "error")
        self.store._rag.assert_not_awaited()

    async def test_unsupported_uploads_are_refused_before_saving_the_batch(self):
        with self.assertRaisesRegex(ValueError, "PDF, Markdown and TXT"):
            await self.store.save_uploads("files", [
                ("valid.txt", b"valid", "active"), ("sample.csv", b"foo,bar", "active"),
            ])
        self.assertEqual(self.store.entries, {})
        with self.assertRaisesRegex(ValueError, "valid PDF"):
            await self.store.save_uploads("files", [("sample.pdf", b"not a pdf", "active")])

    async def test_voice_and_interview_metadata_preserve_the_selected_status(self):
        voice = self.store.save_voice_note(
            "Winterdienst am Rathaus", {"title": "Rathaus", "places": ["Rathaus"]},
            document_status="draft",
        )
        interview = self.store.save_interview(
            "Test person", "Rathaus", "**RULE:** Test", places=["Rathaus"], tags=["winterdienst"],
            document_status="inactive",
        )
        docs = {doc["path"]: doc for doc in self.store.documents()}
        self.assertEqual(docs[voice]["document_status"], "draft")
        self.assertEqual(docs[interview]["document_status"], "inactive")
        self.assertEqual(docs[interview]["tags"], ["winterdienst"])
        self.assertEqual(docs[interview]["ort"], "Rathaus")
        self.assertEqual((await self.store.search("Rathaus"))["results"], [])

    async def test_interview_overview_omits_drafts_but_advances_session(self):
        from app import mcp_tools

        path = self.store.save_interview(
            "Klaus Behrens",
            "Draft topic",
            "# Draft\n\n## Offene Punkte\n- Draft detail",
            document_status="draft",
            session=1,
        )
        self.store.entries[path] = {"id": "kn_draft", "status": "pending"}
        with patch.object(mcp_tools, "store", self.store):
            response = await mcp_tools.person_overview("Klaus Behrens")
        self.assertIn("NEXT INTERVIEW SESSION: 2", response)
        self.assertNotIn("Draft topic", response)
        self.assertNotIn("Draft detail", response)
        self.assertNotIn(path, response)

    async def test_legacy_interview_link_opens_flat_document(self):
        from app import main

        path = "interviews/T02_nachtrag_aenderungen.md"
        self.store.write_text(path, "# Current protocol\n\nCurrent details.")
        with patch.object(main, "store", self.store):
            response = main.view("interviews/klaus-behrens/T02_nachtrag_aenderungen.md")
        self.assertIn("Current details.", response.body.decode())

    async def test_pdf_view_tracks_processing_and_then_shows_the_transcript(self):
        from app import main

        with patch.object(storage, "pdf_page_count", return_value=2):
            [path] = await self.store.save_uploads(
                "files", [("sample.pdf", b"%PDF-1.7 sample", "active")],
            )

        def view() -> str:
            return main.view(path).body.decode()

        async def extract(data, *, on_progress):
            self.assertIn("Preparing PDF pages", view())
            on_progress(1, 2)
            self.assertIn("1 of 2 pages processed", view())
            self.assertIn('max="2" value="1"', view())
            return "Recognizable PDF text."

        async def embed(*args, **kwargs):
            self.assertIn("Text extracted. Indexing for search", view())
            self.assertIn("Recognizable PDF text.", view())
            return httpx.Response(200, json={"status": True})

        self.store._rag = embed
        with patch.object(main, "store", self.store), patch.object(storage, "extract_pdf", extract):
            self.assertIn("Waiting to process PDF", view())
            self.assertNotIn("No text extracted.", view())
            await self.store.sync()
            self.assertIn("Recognizable PDF text.", view())
            self.assertIn("indexed in RAG", view())
            self.assertNotIn('id="pdf-processing"', view())

            self.store.entries[path]["status"] = "error"
            self.store.entries[path].pop("pdf_version")
            self.assertIn("PDF text extraction failed.", view())
            self.store.set_status(path, "draft")
            self.assertIn("Text extraction starts when this document is Active.", view())


class PdfPageTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_pdfs_over_ten_pages(self):
        with patch.object(
            pdf.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"Pages: 11\n"),
        ):
            with self.assertRaisesRegex(ValueError, "at most 10 pages .*11"):
                pdf.pdf_page_count(b"%PDF-1.7")

    async def test_sends_each_page_to_terra_in_order(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": f"Transcription {len(requests)}"}, "finish_reason": "stop"}],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        progress = Mock()
        with (
            patch.dict("os.environ", {"OPENROUTER_KEY": "test-key"}),
            patch.object(pdf, "render_pages", return_value=[b"ab", b"cd"]),
            patch.object(pdf.httpx, "AsyncClient", return_value=client),
        ):
            self.assertEqual(
                await pdf.extract_pdf(b"%PDF-1.7", on_progress=progress),
                "## Page 1\n\nTranscription 1\n\n## Page 2\n\nTranscription 2",
            )
        self.assertEqual([item["model"] for item in requests], ["openai/gpt-6.1-terra"] * 2)
        inputs = [item["messages"][1]["content"][1]["image_url"]["url"] for item in requests]
        self.assertEqual(inputs, ["data:image/jpeg;base64,YWI=", "data:image/jpeg;base64,Y2Q="])
        self.assertEqual([call.args for call in progress.call_args_list], [(0, 2), (1, 2), (2, 2)])

    async def test_refuses_truncated_page_output(self):
        def respond(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "partial text"}, "finish_reason": "length"}],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(RuntimeError, "page 3 exceeded .*output limit"):
                await pdf.transcribe_page(client, b"screenshot", 3)
