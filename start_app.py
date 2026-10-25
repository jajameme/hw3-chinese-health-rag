"""Launch a hidden standalone Flask server and verify its HTTP health endpoint."""

import json
import os
import subprocess
import sys
import time

from config import ROOT, PORT
from progress import write_json

import requests


def capture_if_available():
    try:
        from capture_ui import capture
        capture()
    except Exception as error:
        print(f"Optional browser screenshot failed: {error}")


def main():
    url = f"http://127.0.0.1:{PORT}"
    try:
        response = requests.get(url + "/api/health", timeout=3)
        if response.ok and response.json().get("embedding_model") == "BAAI/bge-m3":
            print("App already running", url)
            capture_if_available()
            return
    except (requests.RequestException, ValueError):
        pass
    (ROOT / "logs").mkdir(exist_ok=True)
    with (ROOT / "logs" / "app.log").open("ab") as log:
        child = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=os.environ.copy(),
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    write_json(ROOT / "logs" / "app-process.json", {"pid": child.pid, "url": url})
    for _ in range(40):
        try:
            response = requests.get(url + "/api/health", timeout=2)
            if response.ok and response.json().get("embedding_model") == "BAAI/bge-m3":
                print("App ready", url)
                capture_if_available()
                return
        except (requests.RequestException, ValueError):
            pass
        if child.poll() is not None:
            raise RuntimeError("App failed to start; inspect logs/app.log and port conflicts")
        time.sleep(1)
    raise RuntimeError("App health check timed out")


if __name__ == "__main__":
    main()
