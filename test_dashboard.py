"""Tests for dashboard.py -- TDD for Task 4."""

import json
import unittest
from unittest.mock import patch, MagicMock


class TestDashboardImports(unittest.TestCase):
    """1. Module imports successfully."""

    def test_imports(self):
        """dashboard.py can be imported without errors (except rich)."""
        import dashboard as _  # noqa: F401


class TestQueueDepthParser(unittest.TestCase):
    """2. Queue depth parser: given mocked subprocess output, returns int."""

    def setUp(self):
        from dashboard import parse_queue_depth
        self.parse = parse_queue_depth

    def test_valid_number(self):
        self.assertEqual(self.parse("42\n"), 42)

    def test_valid_number_stripped(self):
        self.assertEqual(self.parse("  7  \n"), 7)

    def test_zero(self):
        self.assertEqual(self.parse("0\n"), 0)

    def test_error_output(self):
        """Gracefully handles error messages instead of a number."""
        self.assertEqual(self.parse("Error: connection refused"), 0)

    def test_empty_output(self):
        self.assertEqual(self.parse(""), 0)


class TestPodListParser(unittest.TestCase):
    """3. Pod list parser: given mocked kubectl JSON, returns correct pod dicts."""

    def setUp(self):
        from dashboard import parse_pods
        self.parse = parse_pods

    def test_single_pod(self):
        kubectl_json = json.dumps({
            "items": [
                {
                    "metadata": {"name": "worker-abc"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {"ready": True, "state": {"running": {}}}
                        ]
                    }
                }
            ]
        })
        pods = self.parse(kubectl_json)
        self.assertEqual(len(pods), 1)
        self.assertEqual(pods[0]["name"], "worker-abc")
        self.assertEqual(pods[0]["status"], "Running")
        self.assertTrue(pods[0]["ready"])

    def test_multiple_pods(self):
        kubectl_json = json.dumps({
            "items": [
                {
                    "metadata": {"name": "worker-abc"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"ready": True, "state": {"running": {}}}]
                    }
                },
                {
                    "metadata": {"name": "worker-def"},
                    "status": {
                        "phase": "Pending",
                        "containerStatuses": [{"ready": False, "state": {"waiting": {}}}]
                    }
                }
            ]
        })
        pods = self.parse(kubectl_json)
        self.assertEqual(len(pods), 2)
        self.assertEqual(pods[1]["name"], "worker-def")
        self.assertEqual(pods[1]["status"], "Pending")
        self.assertFalse(pods[1]["ready"])

    def test_no_pods(self):
        kubectl_json = json.dumps({"items": []})
        pods = self.parse(kubectl_json)
        self.assertEqual(pods, [])

    def test_no_container_statuses(self):
        """Pod with no containerStatuses still parses."""
        kubectl_json = json.dumps({
            "items": [
                {
                    "metadata": {"name": "worker-xyz"},
                    "status": {"phase": "Running"}
                }
            ]
        })
        pods = self.parse(kubectl_json)
        self.assertEqual(len(pods), 1)
        self.assertFalse(pods[0]["ready"])

    def test_invalid_json(self):
        self.assertEqual(self.parse("not json"), [])

    def test_empty_string(self):
        self.assertEqual(self.parse(""), [])


class TestScaleDetector(unittest.TestCase):
    """4. Scale detector: pod count changes trigger correct events."""

    def setUp(self):
        from dashboard import detect_scale
        self.detect = detect_scale

    def test_no_change(self):
        self.assertIsNone(self.detect(3, 3))

    def test_scale_up(self):
        event = self.detect(2, 5)
        self.assertIsNotNone(event)
        self.assertIn("scaling up", event.lower())
        self.assertIn("2", event)
        self.assertIn("5", event)

    def test_scale_down(self):
        event = self.detect(5, 2)
        self.assertIsNotNone(event)
        self.assertIn("scaling down", event.lower())
        self.assertIn("5", event)
        self.assertIn("2", event)

    def test_from_zero(self):
        """Scaling from 0 pods is a scale up."""
        event = self.detect(0, 3)
        self.assertIn("scaling up", event.lower())

    def test_to_zero(self):
        """Scaling down to 0 pods is a scale down."""
        event = self.detect(3, 0)
        self.assertIn("scaling down", event.lower())

    def test_initial_none(self):
        """First call (old=None) returns an informational event."""
        event = self.detect(None, 3)
        self.assertIsNotNone(event)
        self.assertIn("initial", event.lower())


class TestJobProducer(unittest.TestCase):
    """5. Job producer: correctly formats LPUSH commands."""

    def test_push_ten(self):
        """build_lpush_command(10) produces 10 LPUSH calls."""
        from dashboard import build_lpush_command
        cmds = build_lpush_command(10)
        self.assertEqual(len(cmds), 10)
        for cmd in cmds:
            self.assertIn("LPUSH", cmd)
            self.assertIn("keda:queue", cmd)
            self.assertIn("job:", cmd)

    def test_push_hundred(self):
        from dashboard import build_lpush_command
        cmds = build_lpush_command(100)
        self.assertEqual(len(cmds), 100)

    def test_jobs_have_unique_ids(self):
        from dashboard import build_lpush_command
        cmds = build_lpush_command(5)
        ids = [cmd.split("job:")[1] for cmd in cmds]
        self.assertEqual(len(set(ids)), 5, "Every job should have a unique ID")

    def test_drain_command(self):
        from dashboard import build_drain_command
        cmd = build_drain_command()
        self.assertIn("DEL", cmd)
        self.assertIn("keda:queue", cmd)


class TestQueueBarRenderer(unittest.TestCase):
    """6. render_queue_bar: unicode block bar rendering."""

    def setUp(self):
        from dashboard import render_queue_bar
        self.render_bar = render_queue_bar

    def test_empty_queue(self):
        bar = self.render_bar(0)
        # All empty blocks, no fill
        self.assertIn("░", str(bar))
        self.assertNotIn("█", str(bar))

    def test_partial_fill(self):
        bar = self.render_bar(50, max_visible=100, bar_width=20)
        text = str(bar)
        # ~50% filled
        self.assertIn("█", text)
        self.assertIn("50", text)

    def test_full_queue(self):
        bar = self.render_bar(100)
        text = str(bar)
        self.assertIn("100", text)

    def test_clamped_queue(self):
        bar = self.render_bar(150, max_visible=100, bar_width=20)
        text = str(bar)
        self.assertIn("150", text)

    def test_custom_bar_width(self):
        bar = self.render_bar(10, max_visible=100, bar_width=10)
        # Bar renders without error
        self.assertIsNotNone(bar)


class TestHPAParser(unittest.TestCase):
    """7. parse_hpa: parse kubectl get hpa JSON."""

    def setUp(self):
        from dashboard import parse_hpa
        self.parse = parse_hpa

    def test_valid_hpa(self):
        hpa_json = json.dumps({
            "items": [{
                "metadata": {"name": "keda-hpa-worker"},
                "spec": {"minReplicas": 1, "maxReplicas": 10},
                "status": {"currentReplicas": 3, "desiredReplicas": 5}
            }]
        })
        hpa = self.parse(hpa_json)
        self.assertIsNotNone(hpa)
        self.assertEqual(hpa["min"], 1)
        self.assertEqual(hpa["max"], 10)
        self.assertEqual(hpa["current"], 3)
        self.assertEqual(hpa["desired"], 5)

    def test_no_items(self):
        hpa = self.parse(json.dumps({"items": []}))
        self.assertIsNone(hpa)

    def test_invalid_json(self):
        hpa = self.parse("not json")
        self.assertIsNone(hpa)

    def test_empty_input(self):
        hpa = self.parse("")
        self.assertIsNone(hpa)


class TestAddLog(unittest.TestCase):
    """8. add_log: timestamped log entries."""

    def setUp(self):
        from dashboard import add_log
        self.add_log = add_log

    def test_log_has_timestamp(self):
        state = {"log": []}
        self.add_log(state, "green", "Test message")
        self.assertEqual(len(state["log"]), 1)
        style, msg = state["log"][0]
        self.assertEqual(style, "green")
        self.assertIn("Test message", msg)
        self.assertRegex(msg, r"\[\d{2}:\d{2}:\d{2}\]")

    def test_log_rolling_buffer(self):
        state = {"log": []}
        for i in range(25):
            self.add_log(state, "green", f"Msg {i}")
        self.assertEqual(len(state["log"]), 20)
        self.assertIn("Msg 24", state["log"][-1][1])


if __name__ == "__main__":
    unittest.main()
