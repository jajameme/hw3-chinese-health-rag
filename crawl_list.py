"""Bounded, cached list crawling across three news categories."""

import argparse
import json

from config import ROOT, SITE, CHANNELS
from crawler import Fetcher, parse_list
from progress import update, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=6)
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.pages <= 20 or not 1 <= args.limit <= 300:
        parser.error("pages must be 1..20 and limit 1..300")
    fetcher = Fetcher()
    cached = ROOT / ".cache" / "lists"
    cached.mkdir(parents=True, exist_ok=True)
    items = {}
    page_count = 0
    # Round-robin pages ensure the target does not consist of only one category.
    for page in range(1, args.pages + 1):
        for category, channel in CHANNELS.items():
            path = cached / f"{channel}-{page}.json"
            if path.exists():
                batch = json.loads(path.read_text(encoding="utf-8"))
            else:
                html = fetcher.get(f"{SITE}/channel/{channel}/{page}")
                batch = parse_list(html, category)
                if not batch:
                    raise ValueError(f"No list items on {category} page {page}; selectors may have changed")
                write_json(path, batch)
            for row in batch:
                if len(items) < args.limit:
                    items.setdefault(row["link"], row)
            page_count += 1
            write_json(ROOT / "news_list.json", list(items.values()))
            update("crawl_list", len(items), args.limit, f"{category}：第 {page} 頁", pages=page_count)
            if len(items) >= args.limit:
                return
    if len(items) < args.limit:
        print(f"Page cap reached: {len(items)} unique articles found (target {args.limit})")


if __name__ == "__main__":
    main()
