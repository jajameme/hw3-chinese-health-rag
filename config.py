"""Project paths and settings. Import before Hugging Face libraries."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
DB_PATH = ROOT / "news.db"
INDEX_PATH = ROOT / "vector.index"
MANIFEST_PATH = ROOT / "vector.meta.json"
EMBED_MODEL = "BAAI/bge-m3"
OLLAMA_HOST = os.environ.get("HW3_OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("HW3_MODEL", "gemma4:e2b-it-q4_K_M")
PORT = int(os.environ.get("HW3_PORT", "5000"))
SITE = "https://www.healthnews.com.tw"
CHANNELS = {
    "醫療疾病": "782fe66c-4593-806a-7995-0a9b0ecbc4ce",
    "健康保健": "ac24e543-bc5a-dbd5-0aab-a73a4e109c6d",
    "營養飲食": "f72c8a39-1105-2663-255a-b925ad108bac",
}
USER_AGENT = "HW3HealthRAG/1.0 (educational; limited crawl; Python requests)"
DISCLAIMER = "本回答依新聞資料整理，僅供健康資訊參考，不能取代醫師診斷、個別醫療建議或處方。"
