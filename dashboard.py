#!/usr/bin/env python3
"""KEDA Demo Dashboard -- robust real-time TUI using Rich.

This version keeps the testable functions from the original file, but fixes a few
practical issues that make the dashboard look unreliable during a live demo:

- kubectl failures are surfaced in the UI instead of being silently swallowed;
- LPUSH uses one redis-cli call with multiple jobs, instead of 10/100 kubectl execs;
- the HPA parser accepts both `kubectl get hpa -o json` list output and a single HPA;
- styles are preserved in panels by rendering Rich objects directly;
- namespace, labels, Redis deployment, HPA name and queue key are configurable;
- the dashboard can run without the alternate screen by default, which is easier
  during teaching/demos.
"""

from __future__ import annotations

# Keep the demo script self-contained, but avoid hiding installation failures.
try:
    import rich  # noqa: F401
except ImportError:  # pragma: no cover - depends on target environment
    import subprocess as _sp
    import sys as _sys

    install = _sp.run(
        [_sys.executable, "-m", "pip", "install", "rich", "-q"],
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        print("Rich is required. Install it with: python -m pip install rich", file=_sys.stderr)
        print(install.stderr, file=_sys.stderr)
        raise

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# ============================================================
# Configuration
# ============================================================


CONFIG: dict[str, Any] = {
    "namespace": os.getenv("KEDA_DASHBOARD_NAMESPACE", "default"),
    "redis_deploy": os.getenv("KEDA_DASHBOARD_REDIS_DEPLOY", "redis"),
    "queue_key": os.getenv("KEDA_DASHBOARD_QUEUE_KEY", "keda:queue"),
    "worker_label": os.getenv("KEDA_DASHBOARD_WORKER_LABEL", "app=worker"),
    "worker_name": os.getenv("KEDA_DASHBOARD_WORKER_NAME", "worker"),
    "hpa_name": os.getenv("KEDA_DASHBOARD_HPA", ""),
    "interval": float(os.getenv("KEDA_DASHBOARD_INTERVAL", "1.0")),
    "max_pods": int(os.getenv("KEDA_DASHBOARD_MAX_PODS", "6")),
    "max_log_lines": int(os.getenv("KEDA_DASHBOARD_MAX_LOG_LINES", "6")),
    "ascii_boxes": os.getenv("KEDA_DASHBOARD_ASCII", "1") not in ("0", "false", "False", "no"),
}


@dataclass
class CommandResult:
    """Result of a kubectl invocation."""

    command: list[str]
    stdout: str = ""
    stderr: str = ""
    returncode: int = 1
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def short_error(self) -> str:
        if self.timed_out:
            return "timeout"
        text = (self.stderr or self.stdout or "command failed").strip()
        return text.splitlines()[-1] if text else "command failed"


# ============================================================
# Pure / Testable Functions
# ============================================================


def parse_queue_depth(output):
    """Parse raw redis-cli LLEN output into an int."""
    if not output or not output.strip():
        return 0
    try:
        return int(output.strip())
    except (ValueError, TypeError):
        return 0


def parse_pods(json_str):
    """Parse kubectl get pods -o json into a list of pod dicts."""
    if not json_str:
        return []
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return []

    pods = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})
        container_statuses = status.get("containerStatuses") or []
        ready_count = sum(1 for cs in container_statuses if cs.get("ready"))
        total_count = len(container_statuses)
        restart_count = sum(int(cs.get("restartCount", 0) or 0) for cs in container_statuses)
        creation_ts = meta.get("creationTimestamp")
        pods.append({
            "name": meta.get("name", "?"),
            "status": status.get("phase", "Unknown"),
            "ready": ready_count > 0 if total_count else False,
            "ready_count": ready_count,
            "total_count": total_count,
            "restarts": restart_count,
            "age": _human_age(creation_ts),
        })
    return pods


def detect_scale(old_count, new_count):
    """Return a log-worthy scale-event string, or None if unchanged."""
    if old_count is None:
        return f"Initial pod count: {new_count}"
    if new_count == old_count:
        return None
    direction = "up" if new_count > old_count else "down"
    return f"Scaling {direction}: {old_count} -> {new_count} pods"


def build_lpush_command(n):
    """Return a list of redis-cli LPUSH command strings, one per job.

    Kept for compatibility with the existing tests. The live dashboard uses the
    more efficient `build_bulk_lpush_command` below.
    """
    return [_redis_lpush(job_id=str(uuid.uuid4())[:8]) for _ in range(n)]


def _redis_lpush(job_id):
    return f"redis-cli LPUSH {CONFIG['queue_key']} job:{job_id}"


def build_bulk_lpush_command(n):
    """Return a single redis-cli LPUSH command with n job values."""
    values = [f"job:{uuid.uuid4().hex[:12]}" for _ in range(max(0, int(n)))]
    return ["redis-cli", "LPUSH", CONFIG["queue_key"], *values]


def build_drain_command():
    """Return the redis-cli command to delete the queue."""
    return f"redis-cli DEL {CONFIG['queue_key']}"


def render_queue_bar(depth, max_visible=100, bar_width=20):
    """Render a queue depth bar using unicode block characters.

    Returns a Rich Text object. Pure function, testable.
    """
    from rich.text import Text

    try:
        depth = max(0, int(depth))
    except (TypeError, ValueError):
        depth = 0

    ratio = min(depth / max_visible, 1.0) if max_visible > 0 else 0
    filled_width = ratio * bar_width
    full_blocks = int(filled_width)
    partial = filled_width - full_blocks

    bar = "█" * full_blocks

    if partial > 0 and full_blocks < bar_width:
        idx = min(int(partial * 8), 7)
        bar += ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"][idx]
        full_blocks += 1

    empty = bar_width - full_blocks
    if empty > 0:
        bar += "░" * empty

    style = "cyan" if depth > 0 else "dim"
    return Text.assemble((bar, style), "  ", (str(depth), "bold cyan"))


def parse_hpa(json_str, target_name=None):
    """Parse kubectl HPA JSON into a dict, or None.

    Accepts both list output from `kubectl get hpa -o json` and single-object
    output from `kubectl get hpa NAME -o json`.
    """
    if not json_str:
        return None
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    if "items" in data:
        items = data.get("items", [])
        if not items:
            return None
        item = _select_hpa(items, target_name)
    else:
        item = data

    if not item:
        return None

    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    target = spec.get("scaleTargetRef", {}) or {}
    return {
        "name": meta.get("name", "?"),
        "target": target.get("name", "?"),
        "current": status.get("currentReplicas", 0),
        "desired": status.get("desiredReplicas", 0),
        "min": spec.get("minReplicas", 0),
        "max": spec.get("maxReplicas", 0),
    }


def _select_hpa(items, target_name=None):
    if target_name:
        for item in items:
            spec = item.get("spec", {})
            meta = item.get("metadata", {})
            if spec.get("scaleTargetRef", {}).get("name") == target_name:
                return item
            if target_name in meta.get("name", ""):
                return item
    return items[0] if items else None


def _human_age(creation_timestamp):
    if not creation_timestamp:
        return "—"
    try:
        created = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - created
    except Exception:
        return "—"

    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


# ============================================================
# Data Collection (subprocess wrappers)
# ============================================================


def _kubectl_cmd(args):
    return ["kubectl", "-n", CONFIG["namespace"], *args]


def _run_kubectl_result(args, timeout=5):
    """Run kubectl and return a CommandResult with stdout, stderr and status."""
    command = _kubectl_cmd(args)
    if shutil.which("kubectl") is None:
        return CommandResult(command=command, stderr="kubectl not found in PATH", returncode=127)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            returncode=124,
            timed_out=True,
        )
    except Exception as exc:  # pragma: no cover - defensive guard for demo use
        return CommandResult(command=command, stderr=str(exc), returncode=1)


def _run_kubectl(args, timeout=5):
    """Run a kubectl command and return stdout, or empty string on error.

    Compatibility wrapper kept for existing tests / callers.
    """
    result = _run_kubectl_result(args, timeout=timeout)
    return result.stdout if result.ok else ""


def _run_kubectl_ok(args, timeout=5):
    """Run kubectl, return (stdout, True) or (diagnostic, False)."""
    result = _run_kubectl_result(args, timeout=timeout)
    return (result.stdout if result.ok else result.short_error(), result.ok)


def get_queue_depth():
    """Query Redis for the current queue depth."""
    result = _run_kubectl_result([
        "exec", f"deploy/{CONFIG['redis_deploy']}", "--",
        "redis-cli", "LLEN", CONFIG["queue_key"],
    ], timeout=4)
    return parse_queue_depth(result.stdout) if result.ok else 0


def get_pods():
    """Fetch worker pod list from the cluster."""
    result = _run_kubectl_result([
        "get", "pods", "-l", CONFIG["worker_label"], "-o", "json",
    ], timeout=5)
    return parse_pods(result.stdout) if result.ok else []


def get_hpa_info():
    """Fetch HPA info for the worker scaled object."""
    if CONFIG.get("hpa_name"):
        args = ["get", "hpa", CONFIG["hpa_name"], "-o", "json"]
    else:
        args = ["get", "hpa", "-o", "json"]
    result = _run_kubectl_result(args, timeout=5)
    return parse_hpa(result.stdout, target_name=CONFIG.get("worker_name")) if result.ok else None


def collect_snapshot():
    """Collect all live metrics and return (snapshot, errors)."""
    errors = []

    redis_result = _run_kubectl_result([
        "exec", f"deploy/{CONFIG['redis_deploy']}", "--",
        "redis-cli", "LLEN", CONFIG["queue_key"],
    ], timeout=4)
    queue_depth = parse_queue_depth(redis_result.stdout) if redis_result.ok else 0
    if not redis_result.ok:
        errors.append(f"Redis: {redis_result.short_error()}")

    pods_result = _run_kubectl_result([
        "get", "pods", "-l", CONFIG["worker_label"], "-o", "json",
    ], timeout=5)
    pods = parse_pods(pods_result.stdout) if pods_result.ok else []
    if not pods_result.ok:
        errors.append(f"Pods: {pods_result.short_error()}")

    if CONFIG.get("hpa_name"):
        hpa_args = ["get", "hpa", CONFIG["hpa_name"], "-o", "json"]
    else:
        hpa_args = ["get", "hpa", "-o", "json"]
    hpa_result = _run_kubectl_result(hpa_args, timeout=5)
    hpa = parse_hpa(hpa_result.stdout, target_name=CONFIG.get("worker_name")) if hpa_result.ok else None
    if not hpa_result.ok:
        errors.append(f"HPA: {hpa_result.short_error()}")

    snapshot = {
        "connected": redis_result.ok and pods_result.ok,
        "queue_depth": queue_depth,
        "pods": pods,
        "hpa_info": hpa,
        "last_update": datetime.now().strftime("%H:%M:%S"),
    }
    return snapshot, errors


def push_jobs(n):
    """Push n job items onto the Redis queue using one kubectl exec."""
    cmd = build_bulk_lpush_command(n)
    if len(cmd) <= 3:
        return False, "no jobs to push"
    result = _run_kubectl_result([
        "exec", f"deploy/{CONFIG['redis_deploy']}", "--", *cmd,
    ], timeout=15)
    return result.ok, result.short_error() if not result.ok else f"pushed {n} jobs"


def drain_queue():
    """Delete the entire queue from Redis."""
    result = _run_kubectl_result([
        "exec", f"deploy/{CONFIG['redis_deploy']}", "--",
        "redis-cli", "DEL", CONFIG["queue_key"],
    ], timeout=10)
    return result.ok, result.short_error() if not result.ok else "queue drained"


# ============================================================
# TUI Helpers
# ============================================================


def _panel_box():
    """Return a terminal-safe box style for panels."""
    from rich import box

    return box.ASCII if CONFIG.get("ascii_boxes", True) else box.ROUNDED


def _bounded(value, default, minimum=1):
    """Parse positive integer UI bounds from config/CLI."""
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def add_log(state, style, message):
    """Append a timestamped message to the rolling activity log (max 20)."""
    ts = datetime.now().strftime("%H:%M:%S")
    state.setdefault("log", []).append((style, f"[{ts}] {message}"))
    if len(state["log"]) > 20:
        state["log"] = state["log"][-20:]


def _build_header(state):
    """Build a compact one-line header."""
    from rich.panel import Panel
    from rich.text import Text

    connected = state.get("connected", False)
    dot_style = "bold green" if connected else "bold red"
    connection = "up" if connected else "down"
    pods = state.get("pods", [])
    hpa = state.get("hpa_info") or {}
    hpa_text = "na"
    if hpa:
        hpa_text = f"{hpa.get('current', 'na')}->{hpa.get('desired', 'na')}"
    error_count = len(state.get("errors", []))
    status_text = "ok" if connected and error_count == 0 else f"{error_count}err"
    header = Text.assemble(
        ("●", dot_style),
        "  ",
        (f"conn={connection}", "bold"),
        "   ",
        (f"q={state.get('queue_depth', 0)}", "cyan"),
        "   ",
        (f"pods={len(pods)}", "blue"),
        "   ",
        (f"hpa={hpa_text}", "magenta"),
        "   ",
        (f"ns={CONFIG['namespace']}", "dim"),
        "   ",
        (f"updated={state.get('last_update', '—')}", "dim"),
        "   ",
        (f"status={status_text}", "dim"),
    )
    return Panel(header, border_style="blue", padding=(0, 1), box=_panel_box(), height=3)


def _build_queue_panel(state):
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    depth = state.get("queue_depth", 0)
    items = [render_queue_bar(depth, max_visible=100, bar_width=28)]
    banner = state.get("scale_event_banner")
    if banner:
        items.append(Text(banner, style="bold yellow"))
    else:
        items.append(Text("No recent scale event", style="dim"))
    shortcuts = "1 +10   2 +100   3 drain   q quit"
    if not state.get("keyboard_enabled", True):
        shortcuts += "   kbd unavailable"
    items.append(Text(shortcuts, style="dim"))
    items.append(Text(f"key={CONFIG['queue_key']}", style="dim"))
    items.append(Text(f"redis=deploy/{CONFIG['redis_deploy']}", style="dim"))
    return Panel(
        Group(*items),
        title="Queue + Actions",
        border_style="cyan",
        padding=(0, 1),
        box=_panel_box(),
        height=8,
    )


def _build_pod_panel(state):
    """Build a pod status table with a fixed vertical footprint.

    Rich panels do not provide an internal scroll area in a normal terminal.
    To prevent the dashboard from growing as Kubernetes creates more pods, this
    function shows only the first N pods and adds a summary row for hidden pods.
    """
    from rich.panel import Panel
    from rich.table import Table

    pods = state.get("pods", [])
    max_pods = _bounded(CONFIG.get("max_pods"), default=6)
    visible = pods[:max_pods]
    hidden = max(0, len(pods) - len(visible))

    table = Table(box=None, expand=True, show_header=True, header_style="bold")
    table.add_column("Pod", style="cyan", no_wrap=False, overflow="fold", ratio=3)
    table.add_column("Phase", ratio=1)
    table.add_column("Ready", ratio=1)
    table.add_column("Age", justify="right", ratio=1)

    for pod in visible:
        status_style = "green" if pod["status"] == "Running" else "yellow"
        ready_style = "green" if pod["ready"] else "red"
        ready_text = (
            f"{pod.get('ready_count', 0)}/{pod.get('total_count', 0)}"
            if pod.get("total_count")
            else "No"
        )
        table.add_row(
            pod["name"],
            f"[{status_style}]{pod['status']}[/]",
            f"[{ready_style}]{ready_text}[/]",
            pod.get("age", "—"),
        )

    if hidden:
        table.add_row(
            f"[dim]… {hidden} more pod(s) hidden; use --max-pods to show more[/]",
            "[dim]—[/]",
            "[dim]—[/]",
            "[dim]—[/]",
        )

    if not pods:
        table.add_row(
            f"[dim]No pods match selector {CONFIG['worker_label']}[/]",
            "[dim]—[/]",
            "[dim]—[/]",
            "[dim]—[/]",
        )

    # header + visible rows + optional hidden row, plus borders/padding
    panel_height = max_pods + 5
    return Panel(
        table,
        title=f"Workers ({len(pods)})",
        border_style="blue",
        padding=(0, 1),
        box=_panel_box(),
        height=panel_height,
    )


def _build_log_panel(state):
    """Build a fixed-height rolling activity log."""
    from rich.panel import Panel
    from rich.text import Text

    def clip(line, limit=40):
        if len(line) <= limit:
            return line
        return line[: limit - 1] + "…"

    max_lines = _bounded(CONFIG.get("max_log_lines"), default=6)
    log_text = Text()
    entries = state.get("log", [])
    error_rows = [("red", err) for err in state.get("errors", [])[-max_lines:]]
    if not entries and not error_rows:
        log_text.append("No activity yet\n", style="dim")
    else:
        log_slots = max(0, max_lines - len(error_rows))
        rows = entries[-log_slots:] + error_rows if log_slots else error_rows[-max_lines:]
        for style, msg in rows[-max_lines:]:
            log_text.append(f"{clip(msg)}\n", style=style)
    return Panel(
        log_text,
        title=f"Activity (last {max_lines})",
        border_style="green",
        padding=(0, 1),
        box=_panel_box(),
        height=max_lines + 3,
    )


def render(state):
    """Build a compact, bounded-height dashboard.

    Normal terminal UIs do not have true scrolling inside a box. The dashboard
    therefore keeps each section bounded and shows rolling/truncated content.
    """
    from rich.console import Group
    from rich.table import Table

    main = Table.grid(expand=True)
    main.add_column(ratio=2)
    main.add_column(ratio=1)
    main.add_row(_build_queue_panel(state), _build_pod_panel(state))
    return Group(
        _build_header(state),
        main,
        _build_log_panel(state),
    )


# ============================================================
# Background Threads
# ============================================================


def _keyboard_reader(action_queue, state):
    """Background thread: read single keys from stdin, push to action_queue."""
    if not sys.stdin.isatty():
        state["keyboard_enabled"] = False
        return

    import atexit
    import termios
    import tty

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:
        state["keyboard_enabled"] = False
        return

    def restore():
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass

    atexit.register(restore)
    state["keyboard_enabled"] = True

    try:
        tty.setraw(fd)
        while not state.get("quit"):
            ch = sys.stdin.read(1)
            if ch == "1":
                action_queue.put(("push", 10))
            elif ch == "2":
                action_queue.put(("push", 100))
            elif ch == "3":
                action_queue.put(("drain", None))
            elif ch in ("q", "Q", "\x03"):
                state["quit"] = True
                action_queue.put(("quit", None))
                break
    except Exception:
        state["keyboard_enabled"] = False
    finally:
        restore()


def _background_worker(action_queue, log_queue):
    """Background thread: process long-running actions, report via log_queue."""
    while True:
        action, value = action_queue.get()
        if action == "quit":
            break
        try:
            if action == "push":
                ok, msg = push_jobs(value)
                log_queue.put(("magenta" if ok else "red", msg))
            elif action == "drain":
                ok, msg = drain_queue()
                log_queue.put(("magenta" if ok else "red", msg))
        except Exception as exc:  # pragma: no cover - defensive guard for demo use
            log_queue.put(("red", f"Error: {exc}"))


# ============================================================
# Main
# ============================================================


def _build_arg_parser():
    parser = argparse.ArgumentParser(description="KEDA Redis queue demo dashboard")
    parser.add_argument("--namespace", default=CONFIG["namespace"])
    parser.add_argument("--redis-deploy", default=CONFIG["redis_deploy"])
    parser.add_argument("--queue-key", default=CONFIG["queue_key"])
    parser.add_argument("--worker-label", default=CONFIG["worker_label"])
    parser.add_argument("--worker-name", default=CONFIG["worker_name"], help="Deployment/scale target name used to select the HPA")
    parser.add_argument("--hpa-name", default=CONFIG["hpa_name"], help="Optional explicit HPA name")
    parser.add_argument("--interval", type=float, default=CONFIG["interval"])
    parser.add_argument("--screen", action="store_true", help="Use Rich alternate screen mode")
    parser.add_argument("--max-pods", type=int, default=CONFIG["max_pods"], help="Maximum pod rows shown inside the dashboard")
    parser.add_argument("--max-log-lines", type=int, default=CONFIG["max_log_lines"], help="Maximum activity log rows shown inside the dashboard")
    parser.add_argument("--unicode-boxes", action="store_true", help="Use Unicode borders instead of terminal-safe ASCII borders")
    parser.add_argument("--once", action="store_true", help="Collect once, render once, and exit")
    return parser


def main(argv=None):
    """Entry point: run the Live dashboard loop."""
    from rich.console import Console
    from rich.live import Live

    args = _build_arg_parser().parse_args(argv)
    CONFIG.update({
        "namespace": args.namespace,
        "redis_deploy": args.redis_deploy,
        "queue_key": args.queue_key,
        "worker_label": args.worker_label,
        "worker_name": args.worker_name,
        "hpa_name": args.hpa_name,
        "interval": max(0.25, args.interval),
        "max_pods": max(1, args.max_pods),
        "max_log_lines": max(1, args.max_log_lines),
        "ascii_boxes": not args.unicode_boxes,
    })

    state: dict[str, Any] = {
        "queue_depth": 0,
        "pods": [],
        "hpa_info": None,
        "prev_pod_count": None,
        "scale_events": 0,
        "scale_event_banner": None,
        "scale_event_banner_ttl": 0,
        "log": [],
        "errors": [],
        "connected": False,
        "quit": False,
        "keyboard_enabled": False,
        "last_update": "—",
    }

    action_queue: queue.Queue = queue.Queue()
    log_queue: queue.Queue = queue.Queue()

    add_log(state, "green", "Dashboard started")
    add_log(state, "dim", f"Using selector: {CONFIG['worker_label']}")

    if args.once:
        snapshot, errors = collect_snapshot()
        state.update(snapshot)
        state["errors"] = errors
        Console().print(render(state))
        return 0 if not errors else 1

    kbd_thread = threading.Thread(
        target=_keyboard_reader, args=(action_queue, state), daemon=True,
    )
    kbd_thread.start()

    worker_thread = threading.Thread(
        target=_background_worker, args=(action_queue, log_queue), daemon=True,
    )
    worker_thread.start()

    try:
        with Live(render(state), refresh_per_second=4, screen=args.screen) as live:
            while not state["quit"]:
                try:
                    while True:
                        style, msg = log_queue.get_nowait()
                        add_log(state, style, msg)
                except queue.Empty:
                    pass

                snapshot, errors = collect_snapshot()
                state.update(snapshot)
                state["errors"] = errors

                new_count = len(state["pods"])
                event = detect_scale(state["prev_pod_count"], new_count)
                if event:
                    # Do not count the initial pod count as a scale event.
                    if state["prev_pod_count"] is not None:
                        state["scale_events"] += 1
                    state["scale_event_banner"] = event
                    state["scale_event_banner_ttl"] = max(2, int(4 / CONFIG["interval"]))
                    add_log(state, "yellow", event)
                state["prev_pod_count"] = new_count

                if state.get("scale_event_banner_ttl", 0) > 0:
                    state["scale_event_banner_ttl"] -= 1
                else:
                    state["scale_event_banner"] = None

                live.update(render(state))
                time.sleep(CONFIG["interval"])

    except KeyboardInterrupt:
        state["quit"] = True
    finally:
        try:
            action_queue.put(("quit", None))
        except Exception:
            pass
        Console().print("[green]Dashboard stopped.[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
