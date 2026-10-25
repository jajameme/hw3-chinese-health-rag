"""Install the official portable Ollama, start loopback service and pull model."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from config import ROOT, MODEL, OLLAMA_HOST
from progress import update, write_json

import requests


def download(url, destination, digest=None):
    temporary = destination.with_suffix(destination.suffix + ".part")
    with requests.get(url, stream=True, timeout=(30, 120)) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0))
        received = 0
        last = 0.0
        hasher = hashlib.sha256()
        with temporary.open("wb") as file:
            for chunk in response.iter_content(2**20):
                file.write(chunk)
                hasher.update(chunk)
                received += len(chunk)
                if time.monotonic() - last > 5:
                    update("runtime", received, total or None, "下載官方 Ollama 可攜版（bytes）")
                    last = time.monotonic()
    if digest and hasher.hexdigest() != digest.removeprefix("sha256:"):
        raise RuntimeError("Ollama archive SHA256 does not match the official release digest")
    temporary.replace(destination)


def install():
    existing = shutil.which("ollama")
    portable = ROOT / ".tools" / "ollama" / "ollama.exe"
    if existing or portable.exists():
        return Path(existing) if existing else portable
    if os.name != "nt":
        raise RuntimeError("Install Ollama from https://ollama.com/download first on Linux/macOS")
    releases = requests.get("https://api.github.com/repos/ollama/ollama/releases/latest", timeout=30)
    releases.raise_for_status()
    release = releases.json()
    asset = next(a for a in release["assets"] if a["name"] == "ollama-windows-amd64.zip")
    tools = ROOT / ".tools"
    tools.mkdir(exist_ok=True)
    archive = tools / "ollama-windows-amd64.zip"
    if not archive.exists():
        download(asset["browser_download_url"], archive, asset.get("digest"))
    output = portable.parent.resolve()
    output.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zip_file:
        for item in zip_file.infolist():
            # The local driver provides CUDA 12.8; skip the unused CUDA 13 runtime.
            if "/cuda_v13/" in "/" + item.filename.replace("\\", "/"):
                continue
            resolved = (output / item.filename).resolve()
            if not resolved.is_relative_to(output):
                raise RuntimeError("Archive contains a path outside the runtime directory")
            zip_file.extract(item, output)
    if not portable.exists():
        raise RuntimeError("Portable Ollama executable was not found after extraction")
    write_json(tools / "ollama-release.json", {"version": release["tag_name"],
               "download_url": asset["browser_download_url"], "sha256": asset.get("digest")})
    archive.unlink()
    return portable


def available():
    try:
        return requests.get(OLLAMA_HOST + "/api/version", timeout=3).ok
    except requests.RequestException:
        return False


def ensure_service():
    if available():
        return
    executable = install()
    if urlparse(OLLAMA_HOST).hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Configured remote Ollama is unavailable; cannot start it locally")
    env = os.environ.copy()
    env.update(OLLAMA_HOST=urlparse(OLLAMA_HOST).netloc,
               OLLAMA_MODELS=str(ROOT / ".cache" / "ollama"),
               OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1",
               OLLAMA_CONTEXT_LENGTH="4096", OLLAMA_NO_CLOUD="1")
    (ROOT / "logs").mkdir(exist_ok=True)
    with (ROOT / "logs" / "ollama.log").open("ab") as log:
        child = subprocess.Popen([str(executable), "serve"], cwd=ROOT, env=env,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    write_json(ROOT / "logs" / "ollama-process.json", {"pid": child.pid, "host": OLLAMA_HOST})
    for _ in range(60):
        if available():
            return
        if child.poll() is not None:
            raise RuntimeError("Ollama service exited; see logs/ollama.log")
        time.sleep(1)
    raise RuntimeError("Ollama did not become available within 60 seconds")


def pull():
    ensure_service()
    with requests.post(OLLAMA_HOST + "/api/pull", json={"model": MODEL, "stream": True},
                       stream=True, timeout=(20, 600)) as response:
        response.raise_for_status()
        last = 0.0
        for line in response.iter_lines():
            if not line:
                continue
            item = json.loads(line)
            if item.get("error"):
                raise RuntimeError(item["error"])
            if time.monotonic() - last > 5 or item.get("status") == "success":
                update("model_download", item.get("completed", 0), item.get("total"),
                       item.get("status", ""), model=MODEL)
                last = time.monotonic()


def unload():
    if available():
        response = requests.post(OLLAMA_HOST + "/api/generate", json={"model": MODEL, "keep_alive": 0}, timeout=90)
        response.raise_for_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--pull", action="store_true")
    parser.add_argument("--unload", action="store_true")
    args = parser.parse_args()
    if args.install:
        update("runtime", detail="檢查／安裝 Ollama")
        ensure_service()
        print("Ollama ready", OLLAMA_HOST)
    elif args.pull:
        pull()
    elif args.unload:
        unload()
