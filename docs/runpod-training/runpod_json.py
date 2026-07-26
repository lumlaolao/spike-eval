#!/usr/bin/env python3
"""Parse `runpodctl ... -o json` payloads for launch-pod.sh.

The JSON shape is this pipeline's top named risk (README, "Known-unverified
spots"): it has never been checked against a real account. So the contract
here is defensive on purpose:

  - the CLI entrypoints NEVER exit non-zero on a bad payload — they print
    nothing and exit 0, so the calling shell (set -e, command substitution)
    always survives to reach its own recovery branch, which prints the
    pod-kill instructions. A parse crash that aborts the launcher before it
    can print `runpodctl pod delete <id>` leaves a billing pod with no
    breadcrumbs.
  - both known shapes are accepted: a bare pod object, or a single-element
    list wrapping it; `id` and `podId` are both tried.

Tested offline in test_runpod_json.py with canned payloads, including the
malformed-shape cases.
"""
import json
import sys


def _unwrap(payload):
    """payload str -> pod dict, or {} if the shape is unrecognizable."""
    d = json.loads(payload)
    if isinstance(d, list):
        d = d[0] if d else {}
    return d if isinstance(d, dict) else {}


def pod_id(payload):
    """Extract the pod id from `runpodctl pod create -o json` output.

    Returns '' if absent. Raises on malformed JSON (CLI wrapper swallows).
    """
    d = _unwrap(payload)
    return str(d.get("id") or d.get("podId") or "")


def ssh_endpoint(payload):
    """Extract (host, port) of the direct-TCP SSH port from
    `runpodctl pod get -o json` output.

    Live-verified 2026-07-26 (runpodctl 2.7.2): the CLI publishes SSH info in
    a TOP-LEVEL `ssh` object ({ip, port, ssh_command, ...}) as soon as the
    pod is scheduled; `runtime.ports` mapping objects only appear in the
    GraphQL API, and the CLI's `ports` field is the requested spec as plain
    strings ("22/tcp") until then. We check the observed shape first, then
    the mapping-object shapes for forward compatibility.

    Returns None until an endpoint is published. Raises on malformed JSON
    (CLI wrapper swallows).
    """
    d = _unwrap(payload)
    s = d.get("ssh") or {}
    if isinstance(s, dict) and s.get("ip") and s.get("port"):
        return str(s["ip"]), str(s["port"])
    ports = (d.get("runtime") or {}).get("ports") or d.get("ports") or []
    for p in ports:
        if not isinstance(p, dict):
            continue
        if str(p.get("privatePort")) == "22" and p.get("ip") and p.get("publicPort"):
            return str(p["ip"]), str(p["publicPort"])
    return None


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode not in ("pod-id", "ssh"):
        print("usage: runpod_json.py {pod-id|ssh} < payload.json", file=sys.stderr)
        return 2
    payload = sys.stdin.read()
    try:
        if mode == "pod-id":
            out = pod_id(payload)
            if out:
                print(out)
        else:
            ep = ssh_endpoint(payload)
            if ep:
                print(ep[0], ep[1])
    except Exception:
        pass  # empty output is the failure signal; caller prints recovery help
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
