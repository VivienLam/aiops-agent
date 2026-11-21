"""Feature flags of the demo: read, change and restore src/flagd/demo.flagd.json.

flagd watches this file and reloads it by itself, no restart needed.
"""

import json
import os
import subprocess
from pathlib import Path

import docker
import httpx

DEMO_DIR = Path(os.environ.get("DEMO_DIR", Path.home() / "opentelemetry-demo"))
FLAGS_REL = "src/flagd/demo.flagd.json"
FLAGS_FILE = DEMO_DIR / FLAGS_REL


def load_flags() -> dict:
    return json.loads(FLAGS_FILE.read_text())["flags"]


def set_flag(flag: str, variant: str) -> None:
    """Make `variant` the default of `flag`. Same formatting as the original file."""
    data = json.loads(FLAGS_FILE.read_text())
    if variant not in data["flags"][flag]["variants"]:
        raise ValueError(f"unknown variant {variant!r} for {flag}")
    data["flags"][flag]["defaultVariant"] = variant
    FLAGS_FILE.write_text(json.dumps(data, indent=2) + "\n")


def flags_file_modified() -> bool:
    """True if the flag file differs from the version in the demo's git repo."""
    result = subprocess.run(["git", "-C", str(DEMO_DIR), "diff", "--quiet", "--", FLAGS_REL])
    return result.returncode != 0


def restore_flags() -> None:
    """All flags back to their original value (git restore of the file only)."""
    subprocess.run(["git", "-C", str(DEMO_DIR), "restore", "--", FLAGS_REL], check=True)


def flagd_url() -> str:
    """flagd's evaluation port (8016) gets a random host port, look it up."""
    client = docker.from_env()
    try:
        port = client.containers.get("flagd").ports["8016/tcp"][0]["HostPort"]
    finally:
        client.close()
    return f"http://localhost:{port}"


def evaluate_flag(flag: str) -> str:
    """Variant that flagd serves right now for this flag."""
    resp = httpx.post(f"{flagd_url()}/ofrep/v1/evaluate/flags/{flag}",
                      json={"context": {}}, timeout=5)
    resp.raise_for_status()
    return resp.json()["variant"]
