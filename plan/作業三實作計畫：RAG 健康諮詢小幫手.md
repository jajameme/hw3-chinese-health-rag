# 作業三實作計畫：RAG 健康諮詢小幫手

Oct 25, 2026 · @Ling hsuan

## 目標與系統流程

爬健康醫療網「新聞分類」存進 SQLite，用地端模型產生摘要並建向量索引，再做一個可用 checkbox 決定是否檢索的多輪問答網頁，每次回答都附上新聞標題、連結、新增時間。以老師 `cases/RAG` 與 `cases/ollama` 為底改寫。

&#91;embedded content: RAG 流程 · 離線 5 步、線上 1 個分支\]

上排只需跑一次；下排每則訊息都走一遍，不勾選時跳過檢索，但來源清單一律由程式附在回答後面。

**GTX 1660 Ti 限制**：6 GB VRAM 放不下「Ollama 模型＋ bge-m3 訓練級用量」同時跑，所以建索引前先停掉 Ollama，線上查詢時 bge-m3 放 CPU、GPU 留給 Ollama。

**假設**：Windows 10/11 + PowerShell，Linux/WSL 差異另註。

## 環境：.venv 與 Ollama

Python 套件裝在 repo 的 `.venv`，地端模型由系統上的 Ollama 提供；6 GB VRAM 上同一時間只讓一個模型佔 GPU。

1. 更新 NVIDIA 驅動（`nvidia-smi` 的 CUDA Version ≥ 12.8），安裝 Python 3.12。
2. 建立並啟用：

   ```powershell
   cd hw3_health_rag
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1     # Linux/WSL: source .venv/bin/activate
   python -m pip install -U pip
   ```
3. 先裝 GPU 版 PyTorch（給 bge-m3 建索引用）：

   ```powershell
   pip install torch --index-url https://download.pytorch.org/whl/cu128
   ```
4. 安裝其他套件（版本沿用老師 repo）：

   ```powershell
   pip install sentence-transformers==6.1.0 faiss-cpu==1.15.1 ollama==0.6.2 requests beautifulsoup4 lxml flask
   ```
5. 用官方安裝程式安裝 Ollama，下載模型：`ollama pull gemma4:e2b`（範例用的 `gemma4:e4b` 4-bit 約需 5 GB 以上，在 6 GB 上可能部分放到 CPU，較慢但可用）。
6. 確認：`ollama run gemma4:e2b "你好"` 有回應、`ollama ps` 顯示跑在 GPU；Python 中 `torch.cuda.is_available()` 為 True。
7. 若 faiss 出現 numpy DLL 錯誤，依老師 `cases/RAG/README.md` 的步驟重裝 numpy。
8. 完成後 `pip freeze > requirements.txt`；`.gitignore` 加入 `.venv/`。

## 檔案結構與資料表

把老師的 `crawl_data.py` 拆成多支獨立腳本，每一步都寫進 SQLite，中斷後可接續，不用從頭重跑。

```text
hw3_health_rag/
├── .venv/              # 不提交
├── init_db.py          # 建立 news.db 資料表
├── crawl_list.py       # 新聞分類列表頁 → news_list.json
├── crawl_articles.py   # 逐篇抓標題、全文、日期 → news.db（已存在的連結跳過）
├── summarize.py        # 標題＋全文 → Ollama 摘要，補 summary 為空的列
├── make_index.py       # 摘要 → bge-m3 向量 → vector.index
├── rag.py              # 檢索、組 prompt、格式化來源的共用函式
├── app.py              # Flask 網頁問答
├── templates/index.html
├── images/
├── requirements.txt
└── README.md
```

**資料表 `news`（SQLite `news.db`）**

| 欄位 | 型別 | 對應作業要求 |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 流水號，同時當 FAISS 向量 id |
| `title` | TEXT NOT NULL | 新聞標題 |
| `link` | TEXT UNIQUE NOT NULL | 新聞連結，UNIQUE 防止重複爬 |
| `content` | TEXT | 文章全文 |
| `summary` | TEXT | 文章摘要（先為 NULL，由 summarize.py 補） |
| `published_at` | TEXT | 文章新增時間（網站上的發布日期） |
| `created_at` | TIMESTAMP DEFAULT CURRENT\_TIMESTAMP | 寫入資料庫的時間 |
| `category`、`reporter`、`img_path` | TEXT | 選填，豐富內容 |

「文章新增時間」可能指網站發布日或寫入時間，兩者都存；回覆附來源時顯示 `published_at`，並在 README 說明。

## 實作步驟

依序完成爬蟲、摘要、建索引、網頁四段；摘要最耗時，可分批過夜跑，期間不要同時用 GPU 做其他事。

1. **爬蟲**（`crawl_list.py`、`crawl_articles.py`，改自 `crawl_data.py`）
   - 先在瀏覽器確認健康醫療網「新聞分類」的 channel 網址，選 1–3 個分類；分頁沿用範例的 `/channel/<id>/<page>` 格式。
   - 移除範例只保留「大腸」的過濾條件。
   - selector 沿用範例（`li.list-item`、`div#article-title`、`div#article-content`），實作前先抽幾頁確認版面沒變。
   - 帶 User-Agent、每次請求間隔 3–8 秒、設頁數上限；先抓 100–300 篇即可展示。
   - 從列表資訊用正規表示式取發布日期存 `published_at`；`link` 已存在就跳過。
2. **摘要**（`summarize.py`）
   - 沿用範例的摘要 prompt：繁體中文、不用 markdown、一段完整文字。
   - 輸入為「標題＋全文」，全文過長時截斷（例如前 3,000 字），`num_ctx` 設 4096–8192。
   - 只處理 `summary IS NULL` 的列，每篇寫回後 commit，中斷可續跑。
3. **建索引**（`make_index.py`）
   - 作業要求向量化「摘要」：範例用的是標題＋全文，這裡改成 `summary`。
   - `BAAI/bge-m3` + `normalize_embeddings=True` + `IndexFlatIP` + `IndexIDMap`（內積即 cosine），向量 id 用資料庫 `id`。
   - 建索引前先 `ollama stop gemma4:e2b` 釋放 VRAM，bge-m3 用 GPU、`batch_size=8`。
   - 範例在 index 已存在時會再 `add` 一次，重跑會產生重複向量：改成每次從頭重建。
4. **網頁問答**（`app.py`、`rag.py`、`index.html`，參考 `cases/ollama/app.py` 的 Flask 串流寫法）
   - 聊天框旁一個「檢索新聞資料庫」checkbox，每則訊息送出時一併送到後端。
   - 勾選：問題用 bge-m3 編碼（查詢時放 CPU，VRAM 留給 Ollama）→ FAISS top-3 → 相似度低於門檻（例如 0.45，實測後調整）的捨棄 → 從 DB 取標題、摘要、連結、日期組成 context。
   - 不勾選：只帶對話歷史，不檢索。
   - system prompt：只根據提供的新聞回答、資料不足時說明不知道、提醒內容不能取代醫師診斷。
   - 對話歷史存在後端（或 Flask session），每輪都帶給 Ollama，達成多輪對話。
   - 來源清單由程式在模型回答後附加，不交給模型生成，避免連結被編造：`- 標題（連結，YYYY-MM-DD）`。
   - 未勾選檢索的那一輪，附上這段對話先前檢索過的來源並標註「本輪未重新檢索」，滿足「每次回答都附來源」。

執行順序：

```powershell
python init_db.py
python crawl_list.py
python crawl_articles.py
python summarize.py
ollama stop gemma4:e2b
python make_index.py
python app.py        # 瀏覽器開 http://127.0.0.1:5000
```

## 展示、驗收與繳交

展示要錄一段 3 輪對話：兩個與爬到新聞相關的不同問題，加一輪請系統整理重點，每輪回覆後面都有條列的標題、連結、時間。

**展示腳本**

1. 勾選檢索，問第 1 個與資料庫新聞相關的問題。
2. 勾選檢索，問第 2 個不同主題的問題。
3. 不勾選，請系統「把剛才問的兩個問題整理成重點並回答」（靠對話歷史完成）。
4. 確認 3 輪回覆都附來源；截圖或錄影放進 README。

**常見問題**

| 症狀 | 原因 | 處理 |
| --- | --- | --- |
| 爬不到內容或欄位是 None | 網站版面改變或被擋 | 重新檢查 selector、加長間隔、降低頻率 |
| 摘要很慢 | 模型部分跑在 CPU | 改用 `gemma4:e2b`、縮短截斷長度、分批過夜跑 |
| 建索引 `CUDA out of memory` | Ollama 還佔著 VRAM | `ollama ps` 確認後 `ollama stop`；或 `batch_size` 降到 4 |
| 檢索結果重複 | 範例重跑會重複 add | 每次從頭重建 index |
| 回答與來源無關 | 相似度門檻太低或 top-k 太大 | 提高門檻、top-k 維持 3 |
| 回答簡體字 | 模型偏簡體 | system prompt 指定繁體 + OpenCC `s2twp` |
| faiss numpy DLL 錯誤 | numpy 版本衝突 | 依 `cases/RAG/README.md` 步驟重裝 |

**GitHub repo**

- 建在你自己的 GitHub 帳號下，獨立一個 repo（建議名稱 `hw3_health_rag`），設為 **Public**，老師可直接開啟連結。
- 不 fork 老師的 `telunyang/python_nlp`，只複製 `cases/RAG`、`cases/ollama` 需要改寫的檔案，README 註明改寫出處。
- GitHub 帳號與最終 repo 名稱：實作建立 repo 時再向你確認。

**繳交檢查**

- [ ] 資料表含流水號、新聞標題、新聞連結、文章全文、文章摘要、文章新增時間
- [ ] 摘要由地端模型產生，向量索引建立在摘要上
- [ ] 網頁有 checkbox 控制是否檢索，支援多輪對話
- [ ] 3 輪展示，每輪都附標題、連結、時間（條列）
- [ ] `news.db`、`vector.index` 超過 100 MB 用 Git LFS，否則直接提交，讓助教能直接執行 `app.py`
- [ ] `.gitignore` 排除 `.venv/`、`__pycache__/`
- [ ] README：資料來源、模型、資料表說明與筆數、套件版本、執行方法、成果截圖
- [ ] repo 連結寄給老師，信件標題含班別與姓名

**參考來源**：[telunyang/python\_nlp](https://github.com/telunyang/python_nlp)（`cases/RAG`、`cases/ollama`）、[Gemma 4 在 Ollama 的版本與記憶體需求](https://www.verdent.ai/guides/gemma-4-ollama)、[PyTorch Get Started](https://pytorch.org/get-started/locally/)
