"""Actions on the demo's containers used by the bench (never by the agent)."""

import re

import docker

from server import COMPOSE_PROJECT

SERVICE_NAME = re.compile(r"^[a-z0-9-]{1,40}$")


def restart_service(service: str) -> None:
    """Restart the container of one compose service of the demo."""
    if not SERVICE_NAME.match(service):
        raise ValueError(f"invalid service name: {service!r}")
    client = docker.from_env()
    try:
        containers = client.containers.list(all=True, filters={"label": [
            f"com.docker.compose.project={COMPOSE_PROJECT}",
            f"com.docker.compose.service={service}",
        ]})
        if len(containers) != 1:
            raise RuntimeError(f"expected 1 container for {service}, found {len(containers)}")
        containers[0].restart(timeout=10)
    finally:
        client.close()
