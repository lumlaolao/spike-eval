#!/usr/bin/env python3
"""Offline tests for runpod_json.py — canned `runpodctl -o json` payloads.

The real JSON shape has never been observed against a live account (the
pipeline's top named risk), so these tests pin the two shapes the parser
accepts AND the failure contract: on malformed input the CLI prints nothing
and exits 0, so launch-pod.sh (set -e) survives to print pod-kill guidance.

Run: python3 docs/runpod-training/test_runpod_json.py
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runpod_json import pod_id, ssh_endpoint  # noqa: E402

MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runpod_json.py")


def run_cli(mode, payload):
    return subprocess.run(
        [sys.executable, MODULE, mode],
        input=payload, capture_output=True, text=True,
    )


class PodIdTests(unittest.TestCase):
    def test_bare_object_id(self):
        self.assertEqual(pod_id('{"id": "abc123", "name": "mtg-x"}'), "abc123")

    def test_bare_object_podid_variant(self):
        self.assertEqual(pod_id('{"podId": "xyz789"}'), "xyz789")

    def test_list_wrapped(self):
        self.assertEqual(pod_id('[{"id": "abc123"}]'), "abc123")

    def test_missing_id(self):
        self.assertEqual(pod_id('{"name": "mtg-x"}'), "")

    def test_empty_list(self):
        self.assertEqual(pod_id("[]"), "")

    def test_non_object(self):
        self.assertEqual(pod_id('"just a string"'), "")

    def test_malformed_raises_at_function_level(self):
        with self.assertRaises(Exception):
            pod_id("error: unauthorized")


class SshEndpointTests(unittest.TestCase):
    RUNTIME_SHAPE = json.dumps({
        "id": "abc123",
        "runtime": {"ports": [
            {"privatePort": 8888, "publicPort": 60111, "ip": "1.2.3.4"},
            {"privatePort": 22, "publicPort": 60123, "ip": "1.2.3.4"},
        ]},
    })
    TOPLEVEL_SHAPE = json.dumps({
        "id": "abc123",
        "ports": [{"privatePort": "22", "publicPort": "60123", "ip": "1.2.3.4"}],
    })

    def test_runtime_ports_shape(self):
        self.assertEqual(ssh_endpoint(self.RUNTIME_SHAPE), ("1.2.3.4", "60123"))

    def test_toplevel_ports_shape_string_fields(self):
        self.assertEqual(ssh_endpoint(self.TOPLEVEL_SHAPE), ("1.2.3.4", "60123"))

    def test_not_ready_yet_no_ports(self):
        self.assertIsNone(ssh_endpoint('{"id": "abc123", "runtime": null}'))

    def test_port_22_without_public_mapping(self):
        payload = json.dumps({"runtime": {"ports": [{"privatePort": 22, "ip": None}]}})
        self.assertIsNone(ssh_endpoint(payload))

    def test_ignores_non_dict_port_entries(self):
        payload = json.dumps({"ports": ["garbage", {"privatePort": 22,
                                                    "publicPort": 60123,
                                                    "ip": "1.2.3.4"}]})
        self.assertEqual(ssh_endpoint(payload), ("1.2.3.4", "60123"))

    def test_malformed_raises_at_function_level(self):
        with self.assertRaises(Exception):
            ssh_endpoint("")


class CliContractTests(unittest.TestCase):
    """The property launch-pod.sh depends on: bad input -> empty stdout, exit 0."""

    def test_malformed_pod_id_exits_zero_empty(self):
        r = run_cli("pod-id", "error: unauthorized")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_malformed_ssh_exits_zero_empty(self):
        r = run_cli("ssh", "<html>502 Bad Gateway</html>")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_good_pod_id_round_trip(self):
        r = run_cli("pod-id", '{"id": "abc123"}')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "abc123")

    def test_good_ssh_round_trip(self):
        r = run_cli("ssh", SshEndpointTests.RUNTIME_SHAPE)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "1.2.3.4 60123")

    def test_bad_mode_exits_two(self):
        r = run_cli("bogus", "{}")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
