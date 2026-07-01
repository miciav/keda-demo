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
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime


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
    return [_redis_lpush(job_id=str(uuid.uuid4())[:8]) for _ in range(n)]


def _redis_lpush(job_id):
    return f"redis-cli LPUSH keda:queue job:{job_id}"


def build_drain_command():
    """Return the redis-cli command to delete the queue."""
    return "redis-cli DEL keda:queue"


def render_queue_bar(depth, max_visible=100, bar_width=20):
    """Render a queue depth bar using unicode block characters.

    Returns a Rich Text object. Pure function, testable.
    """
    from rich.text import Text

    ratio = min(depth / max_visible, 1.0) if max_visible > 0 else 0
    filled_width = ratio * bar_width
    full_blocks = int(filled_width)
    partial = filled_width - full_blocks

    bar = ""
    bar += "█" * full_blocks

    if partial > 0 and full_blocks < bar_width:
        idx = min(int(partial * 8), 7)
        bar += ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"][idx]
        full_blocks += 1

    empty = bar_width - full_blocks
    if empty > 0:
        bar += "░" * empty

    style = "cyan" if depth > 0 else "dim"
    return Text.assemble((bar, style), "  ", (str(depth), "bold cyan"))


def parse_hpa(json_str):
    """Parse kubectl get hpa -o json into a dict, or None."""
    if not json_str:
        return None
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None
    items = data.get("items", [])
    if not items:
        return None
    item = items[0]
    spec = item.get("spec", {})
    status = item.get("status", {})
    return {
        "current": status.get("currentReplicas", 0),
        "desired": status.get("desiredReplicas", 0),
        "min": spec.get("minReplicas", 0),
        "max": spec.get("maxReplicas", 0),
    }


# ============================================================
# Data Collection (subprocess wrappers)
# ============================================================


def _run_kubectl(args, timeout=5):
    """Run a kubectl command and return stdout, or empty string on error."""
    try:
        result = subprocess.run(
            ["kubectl", "-n", "default"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout
    except Exception:
        return ""


def _run_kubectl_ok(args, timeout=5):
    """Run kubectl, return (stdout, True) or ("", False)."""
    try:
        result = subprocess.run(
            ["kubectl", "-n", "default"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout, result.returncode == 0
    except Exception:
        return "", False


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


def get_hpa_info():
    """Fetch HPA info for the worker scaled object."""
    out = _run_kubectl([
        "get", "hpa", "-o", "json",
    ])
    return parse_hpa(out)


def push_jobs(n):
    """Push n job items onto the Redis queue (one kubectl exec per job)."""
    for cmd in build_lpush_command(n):
        _run_kubectl(["exec", "deploy/redis", "--"] + cmd.split(), timeout=10)


def drain_queue():
    """Delete the entire queue from Redis."""
    cmd = build_drain_command()
    _run_kubectl(["exec", "deploy/redis", "--"] + cmd.split())


# ============================================================
# TUI Helpers
# ============================================================


def add_log(state, style, message):
    """Append a timestamped message to the rolling activity log (max 20)."""
    ts = datetime.now().strftime("%H:%M:%S")
    state["log"].append((style, f"[{ts}] {message}"))
    if len(state["log"]) > 20:
        state["log"] = state["log"][-20:]


def _build_header(state):
    """Build the header panel."""
    from rich.panel import Panel
    from rich.text import Text

    connected = state.get("connected", False)
    dot = "[bold green]●[/]" if connected else "[bold red]○[/]"
    status_text = "Connected" if connected else "Disconnected"

    header = Text.assemble(
        (dot, ""),
        "  ",
        ("KEDA Dashboard", "bold white"),
        "  ",
        (f"({status_text})", "dim"),
    )
    return Panel(header, style="on blue", height=3)


def _build_queue_panel(state):
    """Build the queue depth panel."""
    from rich.panel import Panel
    from rich.text import Text

    depth = state.get("queue_depth", 0)
    scale_events = state.get("scale_events", 0)

    bar = render_queue_bar(depth)
    content = [bar]

    # Scale banner
    banner = state.get("scale_event_banner")
    if banner:
        content.append(Text(""))
        content.append(Text(banner, style="bold yellow"))

    content.append(Text(""))
    content.append(Text(f"Scale Events: {scale_events}", style="bold"))

    # HPA info
    hpa = state.get("hpa_info")
    if hpa:
        content.append(Text(""))
        content.append(Text(
            f"HPA: {hpa['current']}/{hpa['min']}→{hpa['max']} "
            f"(desired: {hpa['desired']})",
            style="dim",
        ))

    return Panel(
        "\n".join(str(c) for c in content),
        title="[bold cyan]Queue[/]",
        border_style="cyan",
    )


def _build_pod_panel(state):
    """Build the pod status panel."""
    from rich.panel import Panel
    from rich.table import Table

    table = Table(box=None, expand=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Ready")

    pods = state.get("pods", [])
    for pod in pods:
        status_style = "green" if pod["status"] == "Running" else "yellow"
        ready_style = "green" if pod["ready"] else "red"
        table.add_row(
            pod["name"],
            f"[{status_style}]{pod['status']}[/]",
            f"[{ready_style}]{'Yes' if pod['ready'] else 'No'}[/]",
        )

    if not pods:
        table.add_row(
            "[dim]—[/]", "[dim]no pods[/]", "[dim]—[/]"
        )

    return Panel(
        table,
        title=f"[bold blue]Pods[/] ({len(pods)})",
        border_style="blue",
    )


def _build_log_panel(state):
    """Build the activity log panel."""
    from rich.panel import Panel
    from rich.text import Text

    log_text = Text()
    for style, msg in state.get("log", []):
        log_text.append(f"{msg}\n", style=style)

    return Panel(
        log_text,
        title="[bold green]Activity[/]",
        border_style="green",
    )


def _build_footer(_state):
    """Build the footer panel."""
    from rich.panel import Panel
    from rich.text import Text

    footer = Text.assemble(
        (" [1] ", "bold white on dark_blue"),
        ("+10 jobs", ""),
        "  ",
        (" [2] ", "bold white on dark_blue"),
        ("+100 jobs", ""),
        "  ",
        (" [3] ", "bold white on dark_blue"),
        ("Drain", ""),
        "  ",
        (" [q] ", "bold white on dark_red"),
        ("Quit", ""),
    )
    return Panel(footer, style="on grey23")


def render(state):
    """Build the full Rich Layout for the current state."""
    from rich.layout import Layout

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=3),
    )
    layout["right"].split_column(
        Layout(name="pods", ratio=3),
        Layout(name="log", ratio=2),
    )

    layout["header"].update(_build_header(state))
    layout["left"].update(_build_queue_panel(state))
    layout["pods"].update(_build_pod_panel(state))
    layout["log"].update(_build_log_panel(state))
    layout["footer"].update(_build_footer(state))

    return layout


# ============================================================
# Background Threads
# ============================================================


def _keyboard_reader(action_queue, state):
    """Background thread: read single keys from stdin, push to action_queue."""
    import tty
    import termios
    import atexit

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def restore():
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    atexit.register(restore)

    try:
        tty.setraw(fd)
        while not state.get("quit"):
            ch = sys.stdin.read(1)
            if ch == "1":
                action_queue.put("push10")
            elif ch == "2":
                action_queue.put("push100")
            elif ch == "3":
                action_queue.put("drain")
            elif ch in ("q", "Q", "\x03"):
                state["quit"] = True
                action_queue.put("quit")
                break
    except Exception:
        pass
    finally:
        restore()


def _background_worker(action_queue, log_queue):
    """Background thread: process long-running actions, report via log_queue."""
    while True:
        action = action_queue.get()
        if action == "quit":
            break
        try:
            if action == "push10":
                push_jobs(10)
                log_queue.put(("magenta", "Pushed 10 jobs to queue"))
            elif action == "push100":
                push_jobs(100)
                log_queue.put(("magenta", "Pushed 100 jobs to queue"))
            elif action == "drain":
                drain_queue()
                log_queue.put(("magenta", "Queue drained"))
        except Exception as exc:
            log_queue.put(("red", f"Error: {exc}"))


# ============================================================
# Main
# ============================================================


def main():
    """Entry point: run the Live dashboard loop."""
    from rich.live import Live
    from rich.console import Console

    state: dict = {
        "queue_depth": 0,
        "pods": [],
        "hpa_info": None,
        "prev_pod_count": None,
        "scale_events": 0,
        "scale_event_banner": None,
        "scale_event_banner_ttl": 0,
        "log": [],
        "connected": False,
        "quit": False,
    }

    action_queue = queue.Queue()
    log_queue = queue.Queue()

    add_log(state, "green", "Dashboard started")

    # Start keyboard listener
    kbd_thread = threading.Thread(
        target=_keyboard_reader, args=(action_queue, state), daemon=True,
    )
    kbd_thread.start()

    # Start background worker for push/drain
    worker_thread = threading.Thread(
        target=_background_worker, args=(action_queue, log_queue), daemon=True,
    )
    worker_thread.start()

    try:
        with Live(refresh_per_second=4, screen=True) as live:
            while not state["quit"]:
                # Drain log messages from background worker
                try:
                    while True:
                        style, msg = log_queue.get_nowait()
                        add_log(state, style, msg)
                except queue.Empty:
                    pass

                # Collect metrics
                try:
                    depth_out, ok = _run_kubectl_ok([
                        "exec", "deploy/redis", "--",
                        "redis-cli", "LLEN", "keda:queue",
                    ], timeout=3)
                    state["connected"] = ok
                    state["queue_depth"] = parse_queue_depth(depth_out)

                    state["pods"] = get_pods()
                    state["hpa_info"] = get_hpa_info()

                    new_count = len(state["pods"])
                    event = detect_scale(state["prev_pod_count"], new_count)
                    if event:
                        state["scale_events"] += 1
                        state["scale_event_banner"] = event
                        state["scale_event_banner_ttl"] = 16  # ~4 sec at 250ms
                        add_log(state, "yellow", event)
                    state["prev_pod_count"] = new_count
                except Exception as exc:
                    state["connected"] = False
                    add_log(state, "red", f"Data error: {exc}")

                # Decay scale banner
                if state.get("scale_event_banner_ttl", 0) > 0:
                    state["scale_event_banner_ttl"] -= 1
                else:
                    state["scale_event_banner"] = None

                # Render
                live.update(render(state))
                time.sleep(0.25)

    except KeyboardInterrupt:
        state["quit"] = True
    finally:
        Console().print("[green]Dashboard stopped.[/]")


if __name__ == "__main__":
    main()
