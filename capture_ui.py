"""Capture the actual local UI using a dedicated browser profile, when available."""

import subprocess
from pathlib import Path

from config import ROOT, PORT


def capture():
    candidates = [Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                  Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
    browser = next((p for p in candidates if p.exists()), None)
    if browser is None:
        print("No browser executable found; actual transcript PNG is still available")
        return
    images = ROOT / "images"
    images.mkdir(exist_ok=True)
    pages = [("/", "ui_home.png", 1100)]
    if (ROOT / "results" / "evaluation.json").exists():
        pages.append(("/demo", "ui_demo.png", 3200))
    for route, filename, height in pages:
        output = images / filename
        command = [str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
                   "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                   "--user-data-dir=" + str(ROOT / ".cache" / "browser-profile"),
                   f"--window-size=1440,{height}", "--virtual-time-budget=5000", "--hide-scrollbars",
                   "--screenshot=" + str(output), f"http://127.0.0.1:{PORT}{route}"]
        result = subprocess.run(command, capture_output=True, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not output.exists():
            raise RuntimeError("Browser capture failed: " + result.stderr.decode("utf-8", errors="replace")[-1000:])
        print(output)


if __name__ == "__main__":
    capture()
