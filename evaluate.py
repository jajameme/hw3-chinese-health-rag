"""Real retrieval probes and the required three-turn demo, without fabricating scores."""

import json
import uuid
from pathlib import Path

from config import ROOT, MODEL, EMBED_MODEL
from init_db import connect, counts
from progress import update, write_json
from rag import retrieve, answer_once


def main():
    from runtime import ensure_service
    ensure_service()
    (ROOT / "results").mkdir(exist_ok=True)
    with connect() as db:
        sample = db.execute("SELECT id,title,summary FROM news ORDER BY id LIMIT 12").fetchall()
        categories = db.execute("SELECT category,COUNT(*) AS n FROM news GROUP BY category").fetchall()
        demo_rows = db.execute("SELECT id,title,summary,category FROM news WHERE summary IS NOT NULL ORDER BY id").fetchall()
    probes = []
    for i, row in enumerate(sample, 1):
        hits = retrieve(row["title"])
        probes.append({"query": row["title"], "expected_id": row["id"],
                       "hits": [{"id": h["id"], "score": h["score"]} for h in hits],
                       "hit_at_3": any(h["id"] == row["id"] for h in hits)})
        update("evaluate", i, len(sample) + 3, "以真實標題檢查摘要檢索")
    first = demo_rows[0]
    second = next((row for row in demo_rows if row["category"] != first["category"]), demo_rows[-1])
    questions = [(f"關於「{first['title']}」，新聞提到哪些重點與限制？", True),
                 (f"另外，關於「{second['title']}」，新聞有哪些健康提醒？", True),
                 ("請把剛才兩個不同主題整理成重點，並說明這些新聞無法判斷的事情。", False)]
    session_id = "demo-" + uuid.uuid4().hex
    demo = []
    for number, (question, mode) in enumerate(questions, 1):
        update("evaluate", len(sample) + number - 1, len(sample) + 3, f"實際生成展示第 {number} 輪")
        result = answer_once(session_id, question, mode)
        demo.append({"question": question, **result})
        if not result["sources"]:
            raise RuntimeError(f"Demo turn {number} has no sources at threshold 0.45; inspect retrieval before accepting the demo")
    expected_union = {s["id"] for turn in demo[:2] for s in turn["sources"]}
    if {s["id"] for s in demo[2]["sources"]} != expected_union:
        raise RuntimeError("Third turn lost sources from the previous two turns")
    report = {**counts(), "model": MODEL, "embedding_model": EMBED_MODEL,
              "categories": {row["category"]: row["n"] for row in categories},
              "threshold": 0.45, "top_k": 3, "title_query_probes": probes,
              "title_hit_at_3": sum(p["hit_at_3"] for p in probes) / len(probes),
              "note": "Title probes are a pipeline check, not an independent medical-answer accuracy benchmark.",
              "demo": demo}
    write_json(ROOT / "results" / "evaluation.json", report)
    lines = ["# 三輪實際問答展示", "", f"模型：{MODEL}；來源由程式附加。", ""]
    for i, turn in enumerate(demo, 1):
        lines += [f"## 第 {i} 輪（{'檢索' if turn['used_retrieval'] else '不重新檢索'}）", "",
                  f"使用者：{turn['question']}", "", turn["answer"], "", turn["source_text"], "", turn["disclaimer"], ""]
    (ROOT / "results" / "demo.md").write_text("\n".join(lines), encoding="utf-8")
    render_demo(demo)
    update("evaluate", len(sample) + 3, len(sample) + 3, "完成檢索檢查與三輪展示")


def render_demo(demo):
    from PIL import Image, ImageDraw, ImageFont
    fonts = [Path("C:/Windows/Fonts/msjh.ttc"), ROOT.parent / "fonts" / "NotoSansTC-Regular.otf"]
    selected = next((p for p in fonts if p.exists()), None)
    if selected is None:
        raise FileNotFoundError("Install a Chinese font before rendering the demo")
    body = ImageFont.truetype(str(selected), 25)
    small = ImageFont.truetype(str(selected), 19)
    title = ImageFont.truetype(str(selected), 36)
    scratch = ImageDraw.Draw(Image.new("RGB", (1200,100)))
    blocks = []
    def wrap(text, font, width=1050):
        lines = []
        current = ""
        for char in text:
            if char == "\n" or scratch.textlength(current + char, font=font) > width:
                lines.append(current)
                current = "" if char == "\n" else char
            else:
                current += char
        if current:
            lines.append(current)
        return lines
    for i, turn in enumerate(demo, 1):
        lines = wrap(f"第 {i} 輪｜{turn['question']}\n\n{turn['answer']}", body)
        source_lines = wrap(turn["source_text"], small)
        blocks.append((lines, source_lines, len(lines) * 40 + len(source_lines) * 31 + 65))
    image = Image.new("RGB", (1200, 175 + sum(h + 25 for _,_,h in blocks)), "#f7f8f2")
    draw = ImageDraw.Draw(image)
    draw.text((55,35), "好好讀・健康新聞小幫手｜三輪實際輸出", font=title, fill="#243e35")
    draw.text((55,95), "逐字稿排版圖；前兩輪檢索，第三輪依歷史整理，所有來源由程式附上。", font=small, fill="#738078")
    y = 155
    for lines, source_lines, height in blocks:
        draw.rounded_rectangle((35,y,1165,y+height), radius=15, fill="#edf1e7")
        offset = y + 20
        for line in lines:
            draw.text((60,offset),line,font=body,fill="#243e35"); offset += 40
        offset += 12
        for line in source_lines:
            draw.text((60,offset),line,font=small,fill="#557151"); offset += 31
        y += height + 25
    (ROOT / "images").mkdir(exist_ok=True)
    image.save(ROOT / "images" / "demo_chat.png")


if __name__ == "__main__":
    main()
