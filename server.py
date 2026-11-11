import math
import logging
import re

import docker
import httpx
from mcp.server.mcpserver import MCPServer

PROMETHEUS_URL = "http://localhost:9090"
COMPOSE_PROJECT = "opentelemetry-demo"
SERVICE_NAME = re.compile(r"^[a-z0-9-]{1,40}$")

mcp = MCPServer("aiops-observability")
logging.getLogger("httpx").setLevel(logging.WARNING)


def prom_scalar(query: str) -> float | None:
    """Run an instant PromQL query and return the first value, or None if empty."""
    resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=10)
    resp.raise_for_status()
    result = resp.json()["data"]["result"]
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if math.isnan(value) else value


def prom_by_service(query: str) -> dict[str, float]:
    """Run an instant PromQL query grouped by service_name, return {service: value}."""
    resp = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=10)
    resp.raise_for_status()
    out = {}
    for row in resp.json()["data"]["result"]:
        value = float(row["value"][1])
        if not math.isnan(value):
            out[row["metric"].get("service_name", "")] = value
    return out


@mcp.tool()
def list_services(minutes: int = 5) -> dict:
    """All services with traffic and error rate over the last N minutes, worst first.

    Use it first to spot which service is failing, then call get_service_health on it.
    """
    minutes = max(1, min(minutes, 60))
    sel = 'span_kind="SPAN_KIND_SERVER"'
    win = f"[{minutes}m]"
    calls = prom_by_service(
        f"sum by (service_name) (rate(traces_span_metrics_calls_total{{{sel}}}{win}))"
    )
    errors = prom_by_service(
        f"sum by (service_name) (rate(traces_span_metrics_calls_total"
        f'{{{sel},status_code="STATUS_CODE_ERROR"}}{win}))'
    )

    services, ignored = [], 0
    for name, rps in calls.items():
        # Names come from telemetry, not from us: drop anything unexpected.
        if not SERVICE_NAME.match(name):
            ignored += 1
            continue
        if rps <= 0:
            continue
        services.append({
            "service": name,
            "requests_per_second": round(rps, 2),
            "error_rate_percent": round(100 * errors.get(name, 0.0) / rps, 1),
        })
    services.sort(key=lambda s: (-s["error_rate_percent"], -s["requests_per_second"]))

    result = {"window_minutes": minutes, "services": services}
    if ignored:
        result["ignored_invalid_names"] = ignored
    return result


def container_summary(c) -> dict:
    """Short status of one container: state, health, restarts, memory."""
    state = c.attrs["State"]
    info = {
        "service": c.labels.get("com.docker.compose.service", c.name),
        "state": state["Status"],
        "restarts": c.attrs.get("RestartCount", 0),
    }
    if "Health" in state:
        info["health"] = state["Health"]["Status"]
    if state.get("OOMKilled"):
        info["oom_killed"] = True
    if state["Status"] != "running":
        info["exit_code"] = state.get("ExitCode")
        return info

    mem = c.stats(stream=False, one_shot=True).get("memory_stats", {})
    if "usage" in mem:
        # Same as `docker stats`: page cache that can be freed is not counted.
        used = mem["usage"] - mem.get("stats", {}).get("inactive_file", 0)
        info["memory_used_mb"] = round(used / 2**20)
        limit = c.attrs["HostConfig"].get("Memory") or 0
        if limit:
            info["memory_limit_mb"] = round(limit / 2**20)
            info["memory_used_percent"] = round(100 * used / limit, 1)
        else:
            info["memory_limit_mb"] = None
    return info


def problem_score(info: dict) -> tuple:
    """Sort key: broken containers first, then high memory, then restarts."""
    broken = (info["state"] != "running" or info.get("health") == "unhealthy"
              or info.get("oom_killed", False))
    return (not broken, -info.get("memory_used_percent", 0), -info["restarts"])


@mcp.tool()
def get_container_status(service: str | None = None) -> dict:
    """Docker state of the demo containers: running or not, health, restarts, memory used vs limit.

    Give a service name (e.g. "cart", "kafka") for one container, or nothing for all of them,
    problems first. Useful to spot crashes, restart loops or memory pressure.
    """
    filters = {"label": f"com.docker.compose.project={COMPOSE_PROJECT}"}
    if service is not None:
        if not SERVICE_NAME.match(service):
            return {"error": f"invalid service name: {service!r}"}
        filters["label"] = [filters["label"], f"com.docker.compose.service={service}"]

    client = docker.from_env()
    try:
        containers = client.containers.list(all=True, filters=filters)
        infos = [container_summary(c) for c in containers]
    finally:
        client.close()

    if not infos:
        return {"error": f"no container found for service {service!r}" if service
                else "no demo container found, is the demo started?"}
    if service is not None and len(infos) == 1:
        return infos[0]
    infos.sort(key=problem_score)
    return {"containers": infos}


@mcp.tool()
def get_service_health(service: str, minutes: int = 5) -> dict:
    """Traffic, error rate and p95 latency of one service over the last N minutes.

    Computed from the requests the service receives (server spans).
    Service names look like "cart", "payment", "checkout", "frontend".
    """
    if not SERVICE_NAME.match(service):
        return {"error": f"invalid service name: {service!r}"}
    minutes = max(1, min(minutes, 60))
    sel = f'service_name="{service}",span_kind="SPAN_KIND_SERVER"'
    win = f"[{minutes}m]"

    rps = prom_scalar(f"sum(rate(traces_span_metrics_calls_total{{{sel}}}{win}))")
    if not rps:
        return {"service": service, "window_minutes": minutes,
                "status": "no traffic recorded in this window"}
    err_rps = prom_scalar(
        f'sum(rate(traces_span_metrics_calls_total{{{sel},status_code="STATUS_CODE_ERROR"}}{win}))'
    ) or 0.0
    p95 = prom_scalar(
        f"histogram_quantile(0.95, sum by (le) "
        f"(rate(traces_span_metrics_duration_milliseconds_bucket{{{sel}}}{win})))"
    )
    return {
        "service": service,
        "window_minutes": minutes,
        "requests_per_second": round(rps, 2),
        "error_rate_percent": round(100 * err_rps / rps, 1),
        "p95_latency_ms": None if p95 is None else round(p95),
    }


if __name__ == "__main__":
    mcp.run()
