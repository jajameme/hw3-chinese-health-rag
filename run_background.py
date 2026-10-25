"""Detached setup + data pipeline with heartbeat, resumability and honest status."""

import argparse
import importlib.metadata
import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

from config import ROOT, DB_PATH, INDEX_PATH, MODEL, PORT
from progress import now, write_json

STATE = ROOT / "state.json"
LOG = ROOT / "logs" / "pipeline.log"


def counts():
    if not DB_PATH.exists():
        return {"articles": 0, "summaries": 0}
    try:
        with sqlite3.connect(DB_PATH, timeout=1) as db:
            row = db.execute("SELECT COUNT(*),COUNT(NULLIF(TRIM(summary),'')) FROM news").fetchone()
        return {"articles": row[0], "summaries": row[1]}
    except sqlite3.Error:
        return {}


def refresh(state):
    state["updated_at"] = now()
    state["counts"] = counts()
    try:
        p = json.loads((ROOT / "progress.json").read_text(encoding="utf-8"))
        if p.get("operation") == state["stage"] and p.get("updated_at", "") >= state.get("stage_started_at", state.get("resumed_at", state["started_at"])):
            state["progress"] = p
    except (OSError, json.JSONDecodeError):
        pass
    write_json(STATE, state)


def stage(state, name, command, env, log):
    state.update(stage=name, stage_started_at=now(), progress={}, child_pid=None, exit_code=None)
    refresh(state)
    log.write(f"\n[{now()}] START {name}: {' '.join(command)}\n".encode())
    log.flush()
    child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    state["child_pid"] = child.pid
    while child.poll() is None:
        refresh(state)
        time.sleep(5)
    state["exit_code"] = child.returncode
    state["child_pid"] = None
    refresh(state)
    if child.returncode:
        raise RuntimeError(f"{name} failed with exit code {child.returncode}; see logs/pipeline.log")
    state["completed_stages"].append(name)
    log.write(f"[{now()}] END {name}: success\n".encode())
    log.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-setup", action="store_true")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--pages", type=int, default=6)
    parser.add_argument("--from-stage", choices=["crawl_list", "crawl_articles", "summarize", "make_index", "tests", "evaluate", "web"],
                        help="Resume at a specific stage, keeping completed data intact")
    args = parser.parse_args()
    if args.limit < 1 or args.pages < 1:
        parser.error("limit and pages must be positive")
    LOG.parent.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    python = sys.executable
    commands = []
    if not args.skip_setup:
        commands += [
            ("install_torch", [python, "-m", "pip", "install", "torch==2.11.0", "--index-url", "https://download.pytorch.org/whl/cu128"]),
            ("install_packages", [python, "-m", "pip", "install", "-r", "requirements-training.txt"]),
        ]
    commands += [
        ("runtime", [python, "runtime.py", "--install"]),
        ("model_download", [python, "runtime.py", "--pull"]),
        ("crawl_list", [python, "crawl_list.py", "--pages", str(args.pages), "--limit", str(args.limit)]),
        ("crawl_articles", [python, "crawl_articles.py", "--limit", str(args.limit)]),
        ("summarize", [python, "summarize.py"]),
        ("make_index", [python, "make_index.py"]),
        ("tests", [python, "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("evaluate", [python, "evaluate.py"]),
        ("web", [python, "start_app.py"]),
    ]
    previous = {}
    completed_stages = []
    if args.from_stage:
        try:
            previous = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        position = next(i for i, (name, _) in enumerate(commands) if name == args.from_stage)
        commands = commands[position:]
        remaining = {name for name, _ in commands}
        completed_stages = [name for name in previous.get("completed_stages", []) if name not in remaining]
        if args.from_stage in {"tests", "evaluate", "web"}:
            for filename in ("news.db", "vector.index", "vector.meta.json"):
                if not (ROOT / filename).is_file():
                    parser.error(f"Cannot resume at {args.from_stage}: missing {filename}")
            actual = counts()
            if not actual.get("articles") or actual["articles"] != actual["summaries"]:
                parser.error("All stored articles need summaries before resuming evaluation")
    state = {"status": "running", "stage": "starting", "started_at": now(), "updated_at": now(),
             "runner_pid": os.getpid(), "child_pid": None, "model": MODEL,
             "target_articles": args.limit, "completed_stages": completed_stages, "total_stages": len(completed_stages) + len(commands),
             "progress": {}, "counts": counts(), "log": "logs/pipeline.log",
             "app_url": f"http://127.0.0.1:{PORT}", "artifacts": {}}
    if args.from_stage:
        state.update(started_at=previous.get("started_at", state["started_at"]),
                     resumed_at=now(), resume_from_stage=args.from_stage)
    refresh(state)
    try:
        with LOG.open("ab", buffering=0) as log:
            for name, command in commands:
                stage(state, name, command, env, log)
        with (ROOT / "requirements.txt").open("w", encoding="utf-8") as file:
            subprocess.run([python, "-m", "pip", "freeze"], stdout=file, check=True, env=env)
        required = ["news.db", "vector.index", "vector.meta.json", "results/evaluation.json",
                    "results/demo.md", "images/demo_chat.png"]
        for name in required:
            if not (ROOT / name).is_file():
                raise FileNotFoundError(f"Missing final artifact: {name}")
        if counts().get("articles", 0) < args.limit:
            state["warning"] = "Actual article count below target; inspect crawl results."
        state.update(status="completed", stage="completed", completed_at=now(),
                     artifacts={name: name for name in required})
        report = f"\n\n背景流程完成於 {now()}：{counts()}。實際評估見專案 results/evaluation.json；三輪逐字稿見 results/demo.md。模型 {MODEL}。\n"
        docs = ROOT / "docs" if (ROOT / "docs").is_dir() else ROOT.parent / "docs"
        docs.mkdir(exist_ok=True)
        with (docs / "作業三.md").open("a", encoding="utf-8") as file:
            file.write(report)
    except Exception as error:
        state.update(status="failed", failed_stage=state["stage"], error=str(error), completed_at=now())
        with LOG.open("ab") as file:
            file.write(traceback.format_exc().encode("utf-8"))
        with LOG.open("rb") as file:
            file.seek(0, 2)
            file.seek(max(0, file.tell() - 3500))
            state["log_tail"] = file.read().decode("utf-8", errors="replace")
    finally:
        state["child_pid"] = None
        refresh(state)


if __name__ == "__main__":
    handle = (ROOT / ".pipeline.lock").open("a+b")
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("A pipeline is already running")
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        main()
    finally:
        handle.close()
