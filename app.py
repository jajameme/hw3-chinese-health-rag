"""Local Flask chat with SSE and server-side, browser-isolated histories."""

import json
import secrets
from uuid import uuid4

from config import ROOT, PORT, MODEL, EMBED_MODEL, INDEX_PATH, DISCLAIMER
from init_db import init_db, connect, counts

from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context


def create_app():
    app = Flask(__name__)
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    secret = cache / "session-secret.txt"
    if not secret.exists():
        secret.write_text(secrets.token_hex(32), encoding="utf-8")
    app.config.update(SECRET_KEY=secret.read_text(encoding="utf-8"),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      MAX_CONTENT_LENGTH=16000)
    init_db()

    def session_id():
        if "chat_id" not in session:
            session["chat_id"] = uuid4().hex
        return session["chat_id"]

    @app.get("/")
    @app.get("/demo")
    def index():
        session_id()
        return render_template("index.html", model=MODEL)

    @app.get("/api/demo")
    def demo():
        path = ROOT / "results" / "evaluation.json"
        if not path.exists():
            return {"error": "三輪展示仍在背景產生中。"}, 404
        data = json.loads(path.read_text(encoding="utf-8"))
        return jsonify([{**turn, "user": turn["question"]} for turn in data["demo"]])

    @app.get("/api/health")
    def health():
        state = {}
        try:
            state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        busy = state.get("status") == "running"
        return jsonify({**counts(), "ready": INDEX_PATH.exists() and not busy,
                        "pipeline_status": state.get("status", "not_started"),
                        "stage": state.get("stage"), "model": MODEL, "embedding_model": EMBED_MODEL,
                        "notice": DISCLAIMER})

    @app.get("/api/history")
    def get_history():
        from rag import history, source_text
        turns = history(session_id(), limit=30)
        return jsonify([{"user": r["user_text"], "answer": r["answer"],
                         "sources": json.loads(r["sources_json"]),
                         "used_retrieval": bool(r["used_retrieval"]), "disclaimer": DISCLAIMER,
                         "source_text": source_text(json.loads(r["sources_json"]), bool(r["used_retrieval"]))}
                        for r in turns])

    @app.post("/api/clear")
    def clear():
        with connect() as db:
            db.execute("DELETE FROM chat_turns WHERE session_id=?", (session_id(),))
        return {"status": "ok"}

    @app.post("/api/chat/stream")
    def chat_stream():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"error": "請提供 JSON 訊息。"}, 400
        question = data.get("message")
        mode = data.get("use_retrieval")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000 or type(mode) is not bool:
            return {"error": "訊息需為 1–1000 字，use_retrieval 需為布林值。"}, 400
        question = question.strip()
        try:
            state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if state.get("status") == "running":
            return {"error": "資料流程仍在背景執行，完成後再提問，避免與摘要／建索引爭用模型。"}, 503
        conversation_id = session_id()
        from rag import GENERATION_LOCK, prepare, save_turn, source_text
        from llm import stream_chat, normalize, strip_links, chat, shorter_messages, OutputLimitError, ANSWER_TOKEN_LIMIT
        if not GENERATION_LOCK.acquire(blocking=False):
            return {"error": "目前有另一則回答正在生成，請稍後重試。"}, 409

        def event(kind, payload):
            return "event: " + kind + "\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

        @stream_with_context
        def generate():
            try:
                yield event("status", {"message": "正在檢索新聞與準備回答…" if mode else "正在整理先前對話…"})
                messages, sources = prepare(conversation_id, question, mode)
                if not sources:
                    answer = "目前沒有可支持這個問題的新聞資料，無法從現有資料回答。請開啟檢索或調整問題。"
                else:
                    chunks = []
                    try:
                        for text in stream_chat(messages):
                            chunks.append(text)
                            yield event("token", {"text": text})
                        answer = strip_links(normalize("".join(chunks)))
                    except OutputLimitError:
                        yield event("status", {"message": "回答較長，正在精簡後重新生成…"})
                        # The done event replaces all displayed partial text with
                        # the one complete regenerated answer before persisting.
                        answer = strip_links(chat(shorter_messages(messages), max_tokens=ANSWER_TOKEN_LIMIT))
                    if not answer:
                        raise RuntimeError("模型回覆為空，請重試。")
                save_turn(conversation_id, question, answer, sources, mode)
                yield event("done", {"answer": answer, "sources": sources, "used_retrieval": mode,
                                     "source_text": source_text(sources, mode), "disclaimer": DISCLAIMER})
            except Exception as error:
                app.logger.exception("Chat failed")
                yield event("error", {"message": str(error)})
            finally:
                GENERATION_LOCK.release()
        return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
