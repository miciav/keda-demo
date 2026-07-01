#!/usr/bin/env python3
"""KEDA Demo Dashboard -- real-time TUI using Rich."""

# === Auto-install rich if missing ===
try:
    import rich  # noqa: F401
except ImportError:
    import subprocess as _sp
    import sys as _sys
    _sp.run([_sys.executable, "-m", "pip", "install", "rich", "-q"])

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

# === Constants ===

LPUSH_TEMPLATE = "redis-cli LPUSH keda:queue job:{id}"

# === Pure / Testable Functions ===


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
        ready = False
        for cs in status.get("containerStatuses") or []:
            if cs.get("ready"):
                ready = True
        pods.append({
            "name": meta.get("name", "?"),
            "status": status.get("phase", "Unknown"),
            "ready": ready,
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
    """Return a list of redis-cli LPUSH command strings, one per job."""
    return [LPUSH_TEMPLATE.format(id=str(uuid.uuid4())[:8]) for _ in range(n)]


def build_drain_command():
    """Return the redis-cli command to delete the queue."""
    return "redis-cli DEL keda:queue"


# === Data Collection (subprocess wrapper) ===


def _run_kubectl(args):
    """Run a kubectl command and return stdout, or empty string on error."""
    try:
        result = subprocess.run(
            ["kubectl", "-n", "default"] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


def get_queue_depth():
    """Query Redis for the current queue depth."""
    out = _run_kubectl([
        "exec", "deploy/redis", "--",
        "redis-cli", "LLEN", "keda:queue",
    ])
    return parse_queue_depth(out)


def get_pods():
    """Fetch worker pod list from the cluster."""
    out = _run_kubectl([
        "get", "pods", "-l", "app=worker", "-o", "json",
    ])
    return parse_pods(out)


def push_jobs(n):
    """Push n job items onto the Redis queue (one kubectl exec per job)."""
    for cmd in build_lpush_command(n):
        _run_kubectl(["exec", "deploy/redis", "--"] + cmd.split())


def drain_queue():
    """Delete the entire queue from Redis."""
    cmd = build_drain_command()
    _run_kubectl(["exec", "deploy/redis", "--"] + cmd.split())


# === TUI Helpers ===


def add_log(state, style, message):
    """Append a message to the rolling activity log (max 20 entries)."""
    state["log"].append((style, message))
    if len(state["log"]) > 20:
        state["log"] = state["log"][-20:]


def render(state):
    """Build the full Rich Layout for the current state."""
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, BarColumn, TextColumn as Ptc
    from rich.text import Text

    layout = Layout()
    layout.split_column(
        Layout(name="main", ratio=9),
        Layout(name="footer", ratio=1),
    )

    layout["main"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )

    layout["right"].split_column(
        Layout(name="pods", ratio=2),
        Layout(name="log", ratio=1),
    )

    # --- Left: Queue depth bar + scale events ---
    depth = state.get("queue_depth", 0)
    scale_events = state.get("scale_events", 0)

    progress = Progress(
        Ptc("[bold]Depth:"),
        BarColumn(bar_width=30),
        Ptc("[bold]{task.completed}"),
    )
    progress.add_task("queue", total=max(depth, 100), completed=depth)

    layout["left"].update(
        Panel(
            progress,
            title=f"Queue  |  Scale events: {scale_events}",
            border_style="cyan",
        )
    )

    # --- Right top: Pod status table ---
    table = Table(box=None)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Ready")

    for pod in state.get("pods", []):
        status_style = "green" if pod["status"] == "Running" else "yellow"
        ready_style = "green" if pod["ready"] else "red"
        table.add_row(
            pod["name"],
            f"[{status_style}]{pod['status']}[/]",
            f"[{ready_style}]{'Yes' if pod['ready'] else 'No'}[/]",
        )

    if not state.get("pods"):
        table.add_row("[dim]No pods found[/]", "", "")

    layout["pods"].update(Panel(table, title="Pods", border_style="blue"))

    # --- Right bottom: Activity log (rolling 20) ---
    log_text = Text()
    for style, msg in state.get("log", []):
        log_text.append(f"> {msg}\n", style=style)

    layout["log"].update(
        Panel(log_text, title="Activity", border_style="green")
    )

    # --- Footer: keyboard shortcuts ---
    footer = Text(
        " [1] +10 jobs  [2] +100 jobs  [3] Drain queue  [q] Quit",
        style="bold reverse",
    )
    layout["footer"].update(
        Panel(footer, style="on blue")
    )

    return layout


def keyboard_listener(state):
    """Background thread: read single keys from stdin."""
    import tty
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while not state.get("quit"):
            ch = sys.stdin.read(1)
            if ch == "1":
                state["action"] = "push10"
            elif ch == "2":
                state["action"] = "push100"
            elif ch == "3":
                state["action"] = "drain"
            elif ch == "q":
                state["quit"] = True
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    """Entry point: run the Live dashboard loop."""
    from rich.live import Live
    from rich.console import Console

    state: dict = {
        "queue_depth": 0,
        "pods": [],
        "prev_pod_count": None,
        "scale_events": 0,
        "log": [],
        "action": None,
        "quit": False,
    }

    add_log(state, "green", "Dashboard started")

    listener = threading.Thread(
        target=keyboard_listener, args=(state,), daemon=True
    )
    listener.start()

    try:
        with Live(refresh_per_second=4, screen=True) as live:
            while not state["quit"]:
                # --- Collect metrics ---
                try:
                    state["queue_depth"] = get_queue_depth()
                    pods = get_pods()
                    state["pods"] = pods

                    new_count = len(pods)
                    event = detect_scale(state["prev_pod_count"], new_count)
                    if event:
                        state["scale_events"] += 1
                        add_log(state, "yellow", event)
                    state["prev_pod_count"] = new_count
                except Exception as exc:
                    add_log(state, "red", f"Data error: {exc}")

                # --- Process queued actions ---
                action = state.get("action")
                if action == "push10":
                    try:
                        push_jobs(10)
                        add_log(state, "magenta", "Pushed 10 jobs to queue")
                    except Exception as exc:
                        add_log(state, "red", f"Push error: {exc}")
                    state["action"] = None
                elif action == "push100":
                    try:
                        push_jobs(100)
                        add_log(state, "magenta",
                                "Pushed 100 jobs to queue")
                    except Exception as exc:
                        add_log(state, "red", f"Push error: {exc}")
                    state["action"] = None
                elif action == "drain":
                    try:
                        drain_queue()
                        add_log(state, "magenta", "Queue drained")
                    except Exception as exc:
                        add_log(state, "red", f"Drain error: {exc}")
                    state["action"] = None

                # --- Render ---
                live.update(render(state))
                time.sleep(0.25)

    except KeyboardInterrupt:
        state["quit"] = True
    finally:
        Console().print("[green]Dashboard stopped.[/]")


if __name__ == "__main__":
    main()
