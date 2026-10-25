"""Regression checks for provenance, isolation, selectors and index rebuilds."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import init_db
import rag
from crawler import article_url, parse_article, parse_date, parse_list


class ParsingTests(unittest.TestCase):
    def test_reject_external_urls_and_normalize_date(self):
        self.assertEqual(article_url("/article/70107/"), "https://www.healthnews.com.tw/article/70107")
        with self.assertRaises(ValueError):
            article_url("https://evil.example/article/1")
        self.assertEqual(parse_date("記者 2026-2-03 報導"), "2026-02-03")
        self.assertIsNone(parse_date("2026-02-30"))

    def test_selectors_and_publication_time(self):
        html = '<li class="list-item"><div class="list-title"><a href="/article/42">測試標題</a></div><div class="list-info">記者王明報導 2026-10-01</div></li>'
        item = parse_list(html, "健康保健")[0]
        article = '<meta property="article:published_time" content="2026-10-02T12:00:00+08:00"><div id="article-title"><h1>完整標題</h1></div><div id="article-content"><script>delete everything</script><p>' + '真實新聞內文。' * 30 + '</p></div>'
        result = parse_article(article, item)
        self.assertEqual(result["published_at"], "2026-10-02")
        self.assertEqual(result["title"], "完整標題")
        self.assertNotIn("delete everything", result["content"])
        with self.assertRaises(ValueError):
            parse_article("<html>No article</html>", item)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.db"
        init_db.init_db(self.database)
        self.connection_patch = patch("rag.connect", lambda: init_db.connect(self.database))
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        self.temp.cleanup()

    def test_no_retrieval_reuses_only_this_sessions_sources(self):
        source = {"id": 12, "title": "新聞標題", "summary": "新聞摘要", "link": "https://www.healthnews.com.tw/article/12", "published_at": "2026-10-01"}
        rag.save_turn("alice", "問題一", "回答一", [source], True)
        with patch("rag.retrieve", side_effect=AssertionError("Retrieval must not run")):
            messages, selected = rag.prepare("alice", "整理剛才的重點", False)
            self.assertEqual(selected, [source])
            self.assertIn("問題一", str(messages))
            _, outsider_sources = rag.prepare("bob", "有什麼資料？", False)
            self.assertEqual(outsider_sources, [])
        text = rag.source_text(selected, False)
        self.assertIn("本輪未重新檢索", text)
        self.assertIn(source["link"], text)
        self.assertIn(source["published_at"], text)

    def test_unmatched_retrieval_does_not_claim_old_sources_are_new_hits(self):
        source = {"id": 7, "title": "舊資料", "summary": "摘要", "link": "https://www.healthnews.com.tw/article/7", "published_at": "2026-01-01"}
        rag.save_turn("a", "舊問題", "舊回答", [source], True)
        with patch("rag.retrieve", return_value=[]):
            _, sources = rag.prepare("a", "沒有相關新聞的新問題", True)
        self.assertEqual(sources, [])


class WebTests(unittest.TestCase):
    def setUp(self):
        import app
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "test.db"
        original_init = init_db.init_db
        self.patches = [patch("app.ROOT", root),
                        patch("app.init_db", lambda: original_init(self.database)),
                        patch("app.connect", lambda: init_db.connect(self.database)),
                        patch("rag.connect", lambda: init_db.connect(self.database))]
        for p in self.patches:
            p.start()
        self.app = app.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_validation_empty_sources_and_browser_isolation(self):
        self.assertEqual(self.client.post("/api/chat/stream", json={"message": "hi", "use_retrieval": "false"}).status_code, 400)
        response = self.client.post("/api/chat/stream", json={"message": "整理資料", "use_retrieval": False})
        self.assertIn("event: done", response.get_data(as_text=True))
        self.assertEqual(len(self.client.get("/api/history").json), 1)
        outsider = self.app.test_client()
        self.assertEqual(outsider.get("/api/history").json, [])
        self.assertEqual(outsider.post("/api/clear").status_code, 200)
        self.assertEqual(len(self.client.get("/api/history").json), 1)
        self.client.post("/api/clear")
        self.assertEqual(self.client.get("/api/history").json, [])

    def test_stream_length_retry_saves_only_complete_regenerated_answer(self):
        import llm
        source = {"id": 12, "title": "來源標題", "summary": "摘要", "link": "https://www.healthnews.com.tw/article/12", "published_at": "2026-10-01"}
        def truncated_stream(messages):
            yield "尚未完成的長回答"
            raise llm.OutputLimitError("truncated")
        with patch("rag.prepare", return_value=([{"role": "user", "content": "整理重點"}], [source])), patch("llm.stream_chat", side_effect=truncated_stream), patch("llm.chat", return_value="完整的精簡回答"):
            response = self.client.post("/api/chat/stream", json={"message": "整理重點", "use_retrieval": False})
            text = response.get_data(as_text=True)
        self.assertIn("event: done", text)
        turns = self.client.get("/api/history").json
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["answer"], "完整的精簡回答")
        self.assertEqual(turns[0]["sources"], [source])


class GenerationTests(unittest.TestCase):
    def response(self, reason, content):
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {"done": True, "done_reason": reason,
                                      "message": {"content": content}}
        return response

    def test_truncated_answer_regenerates_without_mutating_question(self):
        import llm
        messages = [{"role": "system", "content": "只根據新聞回答"}, {"role": "user", "content": "整理兩個主題"}]
        original = json.loads(json.dumps(messages))
        with patch("llm.requests.post", side_effect=[self.response("length", "partial"), self.response("stop", "完整回答")]) as request:
            answer = llm.chat(messages, max_tokens=1200, retry_on_length=True)
        self.assertEqual(answer, "完整回答")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(messages, original)

    def test_exhausted_length_retry_does_not_save_history(self):
        import llm
        with patch("rag.prepare", return_value=([{"role": "user", "content": "問"}], [{"id": 1}])), patch("rag.save_turn") as save, patch("llm.requests.post", side_effect=[self.response("length", "partial1"), self.response("length", "partial2")]):
            with self.assertRaises(llm.OutputLimitError):
                rag.answer_once("session", "問", False)
        save.assert_not_called()

    def test_stream_without_completion_is_not_accepted(self):
        import llm
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [json.dumps({"message": {"content": "partial"}, "done": False}).encode()]
        with patch("llm.chat", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "提前結束"):
                list(llm.stream_chat([{"role": "user", "content": "問"}]))


@unittest.skipUnless(importlib.util.find_spec("faiss"), "FAISS is installed during background setup")
class IndexTests(unittest.TestCase):
    def test_rebuild_is_idempotent_and_summary_changes_invalidate_index(self):
        import faiss
        import numpy as np
        import make_index
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "news.db"
            init_db.init_db(db_path)
            with init_db.connect(db_path) as db:
                db.execute("INSERT INTO news(id,title,link,content,summary) VALUES (41,'a','https://example/a','content','alpha')")
                db.execute("INSERT INTO news(id,title,link,content,summary) VALUES (99,'b','https://example/b','content','beta')")
            class FakeEncoder:
                def encode(self, texts, **kwargs):
                    return np.array([[1.,0.] if t == "alpha" else [0.,1.] for t in texts], dtype="float32")
            patches = [patch("make_index.connect", lambda: init_db.connect(db_path)),
                       patch("make_index.INDEX_PATH", root / "vector.index"),
                       patch("make_index.MANIFEST_PATH", root / "vector.meta.json"),
                       patch("make_index.update"), patch("runtime.unload"),
                       patch("embeddings.load_encoder", return_value=FakeEncoder())]
            for p in patches:
                p.start()
            try:
                make_index.main()
                make_index.main()
                index = faiss.read_index(str(root / "vector.index"))
                self.assertEqual(index.ntotal, 2)
                _, ids = index.search(np.array([[0.,1.]], dtype="float32"), 1)
                self.assertEqual(int(ids[0,0]), 99)
                with init_db.connect(db_path) as db:
                    db.execute("UPDATE news SET summary='changed' WHERE id=41")
                with patch("rag.connect", lambda: init_db.connect(db_path)), patch("rag.INDEX_PATH", root / "vector.index"), patch("rag.MANIFEST_PATH", root / "vector.meta.json"):
                    with self.assertRaisesRegex(RuntimeError, "摘要資料已更新"):
                        rag.retrieve("question")
            finally:
                for p in reversed(patches):
                    p.stop()


if __name__ == "__main__":
    unittest.main()
