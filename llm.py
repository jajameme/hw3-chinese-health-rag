"""Local-only Ollama text generation; no hosted model or API key needed."""

import json
import re

from config import MODEL, OLLAMA_HOST

import requests

ANSWER_TOKEN_LIMIT = 1200


class OutputLimitError(RuntimeError):
    """A generation was truncated and must not be saved as a complete answer."""


def shorter_messages(messages):
    result = [dict(message) for message in messages]
    instruction = "請重新精簡作答：總共不超過200個中文字，最多三點，直接回答重點和資料限制，不抄寫整份摘要、不列來源清單。"
    if result and result[0]["role"] == "system":
        result[0]["content"] += "\n" + instruction
    else:
        result.insert(0, {"role": "system", "content": instruction})
    return result


def normalize(value):
    from opencc import OpenCC
    return OpenCC("s2twp").convert(value).strip()


def chat(messages, *, stream=False, max_tokens=500, retry_on_length=False):
    payload = {"model": MODEL, "messages": messages, "stream": stream, "think": False,
               "keep_alive": "10m", "options": {"temperature": 0.2, "seed": 42,
               "num_ctx": 4096, "num_predict": max_tokens}}
    attempts = 2 if retry_on_length and not stream else 1
    for attempt in range(attempts):
        if attempt:
            payload["messages"] = shorter_messages(messages)
            print("Retrying truncated generation with a shorter-answer instruction", flush=True)
        response = requests.post(OLLAMA_HOST + "/api/chat", json=payload,
                                 stream=stream, timeout=(15, 600))
        try:
            response.raise_for_status()
            if stream:
                return response
            data = response.json()
        finally:
            if not stream:
                response.close()
        if data.get("error"):
            raise RuntimeError(data["error"])
        if data.get("done_reason") == "length":
            if attempt + 1 < attempts:
                continue
            raise OutputLimitError("Local model output reached its token limit; no partial answer was saved")
        if not data.get("done"):
            raise RuntimeError("Local model response was incomplete; no partial answer was saved")
        content = normalize(data.get("message", {}).get("content", ""))
        if not content:
            raise RuntimeError("Local model returned an empty answer")
        return content


def stream_chat(messages):
    completed = False
    with chat(messages, stream=True, max_tokens=ANSWER_TOKEN_LIMIT) as response:
        for line in response.iter_lines():
            if line:
                item = json.loads(line)
                if item.get("error"):
                    raise RuntimeError(item["error"])
                text = item.get("message", {}).get("content", "")
                if text:
                    yield text
                if item.get("done"):
                    if item.get("done_reason") == "length":
                        raise OutputLimitError("回答超過長度上限；這次未儲存到歷史。")
                    completed = True
    if not completed:
        raise RuntimeError("模型連線提前結束，這次回覆未儲存到歷史。")


def strip_links(value):
    """Only program-owned source cards may carry links in the final answer."""
    value = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", value)
    return re.sub(r"https?://[^\s<>]+", "（請見下方來源）", value)
