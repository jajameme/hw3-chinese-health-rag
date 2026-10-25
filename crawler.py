"""Rate-limited fetching and selectors verified against Healthnews HTML."""

import random
import re
import time
from datetime import date
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from config import SITE, USER_AGENT


def article_url(value):
    result = urlparse(urljoin(SITE, value))
    if result.scheme != "https" or result.hostname != "www.healthnews.com.tw":
        raise ValueError("Unexpected source domain")
    match = re.fullmatch(r"/article/(\d+)/?", result.path)
    if not match:
        raise ValueError("Unexpected article URL")
    return f"{SITE}/article/{match[1]}"


def parse_date(value):
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", value or "")
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def parse_list(html, category):
    soup = BeautifulSoup(html, "lxml")
    result = []
    for item in soup.select("li.list-item"):
        anchor = item.select_one("div.list-title a[href]")
        info = item.select_one("div.list-info")
        if anchor is None:
            continue
        details = info.get_text(" ", strip=True) if info else ""
        try:
            link = article_url(anchor["href"])
        except ValueError:
            continue
        reporter_match = re.search(r"(?:記者)(.+?)(?:報導|整理)", details)
        result.append({"title": anchor.get_text(" ", strip=True), "link": link,
                       "category": category, "published_at": parse_date(details),
                       "reporter": reporter_match.group(1).strip() if reporter_match else None})
    return result


def parse_article(html, listing):
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one("#article-title h1, #article-title")
    content = soup.select_one("#article-content")
    if title is None or content is None:
        raise ValueError("Article title/content selectors returned no results")
    for node in content.select("script,style,iframe,form,ins,.advertisement,.ads"):
        node.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in content.select("p,h2,h3,li")]
    text = "\n".join(p for p in paragraphs if p) or content.get_text("\n", strip=True)
    if len(text) < 100:
        raise ValueError("Article content is too short; refusing to store an empty/ad-only page")
    published = soup.select_one('meta[property="article:published_time"]')
    image = soup.select_one('meta[property="og:image"]')
    result = {**listing, "title": title.get_text(" ", strip=True), "content": text,
              "img_path": image.get("content") if image else None}
    result["published_at"] = parse_date(published.get("content")) if published else listing.get("published_at")
    if not result["published_at"]:
        raise ValueError("No verifiable article publication date")
    return result


class Fetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.last_request = 0.0
        self.robots = RobotFileParser()
        response = self.session.get(SITE + "/robots.txt", timeout=30)
        response.raise_for_status()
        self.robots.parse(response.text.splitlines())

    def get(self, url):
        if not self.robots.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows {url}")
        pause = random.uniform(3, 8) - (time.monotonic() - self.last_request)
        if pause > 0:
            time.sleep(pause)
        self.last_request = time.monotonic()
        response = self.session.get(url, timeout=(15, 45))
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
