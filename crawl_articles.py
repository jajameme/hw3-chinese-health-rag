"""Store each article immediately; skip URLs already committed to SQLite."""

import argparse
import json

from config import ROOT
from crawler import Fetcher, parse_article
from init_db import init_db, connect, counts
from progress import update, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()
    init_db()
    listings = json.loads((ROOT / "news_list.json").read_text(encoding="utf-8"))[:args.limit]
    fetcher = Fetcher()
    failures = []
    for number, row in enumerate(listings, 1):
        with connect() as db:
            exists = db.execute("SELECT 1 FROM news WHERE link=?", (row["link"],)).fetchone()
        if exists:
            update("crawl_articles", number, len(listings), "跳過已存在的新聞")
            continue
        try:
            article = parse_article(fetcher.get(row["link"]), row)
            with connect() as db:
                db.execute("""INSERT OR IGNORE INTO news
                    (title,link,content,published_at,category,reporter,img_path)
                    VALUES (:title,:link,:content,:published_at,:category,:reporter,:img_path)""", article)
            update("crawl_articles", number, len(listings), article["title"])
        except Exception as error:
            failures.append({"link": row["link"], "error": str(error)})
            print(f"Article failed: {row['link']}: {error}", flush=True)
    (ROOT / "results").mkdir(exist_ok=True)
    write_json(ROOT / "results" / "crawl_report.json", {**counts(), "requested": len(listings), "failures": failures})
    if counts()["articles"] == 0 or failures:
        raise RuntimeError(f"{len(failures)} article(s) failed; rerun to retry missing URLs")
    if counts()["articles"] < min(100, args.limit):
        raise RuntimeError("Fewer than 100 articles available; rerun crawl_list.py with a larger page cap")


if __name__ == "__main__":
    main()
