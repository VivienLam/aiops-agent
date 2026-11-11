import math
import logging
import re

import docker
import httpx
from mcp.server.mcpserver import MCPServer

PROMETHEUS_URL = "http://localhost:9090"
COMPOSE_PROJECT = "opentelemetry-demo"
SERVICE_NAME = re.compile(r"^[a-z0-9-]{1,40}$")

# search_logs limits
LOG_INDEX = "otel-logs-*"
LOG_EXCLUDED_SERVICES = ["otelcol-contrib"]  # the collector's own logs are noise
LOG_MAX_MESSAGE_CHARS = 300
LOG_MAX_GROUPS = 20
LOG_MAX_SCANNED = 1000  # raw log lines read before grouping
LOG_MAX_MINUTES = 60
LOG_SEARCH_TEXT = re.compile(r"^[\w .,:;/@'\"()=-]{1,100}$")
# OpenTelemetry severity numbers: each level starts at these values.
SEVERITY_MIN = {"debug": 5, "info": 9, "warn": 13, "error": 17, "fatal": 21}

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


def opensearch_url() -> str:
    """OpenSearch gets a random host port at each demo start, so look it up."""
    client = docker.from_env()
    try:
        port = client.containers.get("opensearch").ports["9200/tcp"][0]["HostPort"]
    finally:
        client.close()
    return f"http://localhost:{port}"


def severity_label(number: int) -> str:
    """Map an OpenTelemetry severity number to a level name."""
    label = "unset"
    for name, start in SEVERITY_MIN.items():
        if number >= start:
            label = name
    if 1 <= number < SEVERITY_MIN["debug"]:
        label = "trace"
    return label


@mcp.tool()
def search_logs(service: str | None = None, min_severity: str | None = None,
                text: str | None = None, minutes: int = 15) -> dict:
    """Search the demo's application logs, grouped by identical message, most severe first.

    Optional filters: service name (e.g. "payment"), min_severity
    ("debug", "info", "warn", "error", "fatal"), text that the message must
    contain (words, not a query syntax), and the time window in minutes.
    Each group gives the service, severity, number of occurrences and the message.
    """
    minutes = max(1, min(minutes, LOG_MAX_MINUTES))
    filters = [{"range": {"@timestamp": {"gte": f"now-{minutes}m"}}}]
    if service is not None:
        if not SERVICE_NAME.match(service):
            return {"error": f"invalid service name: {service!r}"}
        filters.append({"term": {"resource.service.name.keyword": service}})
    if min_severity is not None:
        if min_severity not in SEVERITY_MIN:
            return {"error": f"min_severity must be one of {list(SEVERITY_MIN)}"}
        filters.append({"range": {"severity.number": {"gte": SEVERITY_MIN[min_severity]}}})
    if text is not None:
        if not LOG_SEARCH_TEXT.match(text):
            return {"error": "invalid text: letters, digits, spaces and . , : ; / @ ' \" ( ) = - only, max 100"}
        filters.append({"match": {"body": {"query": text, "operator": "and"}}})

    body = {
        "size": LOG_MAX_SCANNED,
        "track_total_hits": True,
        "query": {"bool": {
            "filter": filters,
            "must_not": [{"terms": {"resource.service.name.keyword": LOG_EXCLUDED_SERVICES}}],
        }},
        "sort": [{"severity.number": {"order": "desc", "unmapped_type": "long"}},
                 {"@timestamp": "desc"}],
        "_source": ["body", "severity.number", "resource.service.name",
                    "attributes.exception.message"],
    }
    resp = httpx.post(f"{opensearch_url()}/{LOG_INDEX}/_search", json=body, timeout=15)
    resp.raise_for_status()
    hits = resp.json()["hits"]

    groups: dict[tuple[str, str], dict] = {}
    for hit in hits["hits"]:
        src = hit["_source"]
        svc = src.get("resource", {}).get("service.name", "unknown")
        message = str(src.get("body", ""))
        exc = src.get("attributes", {}).get("exception.message")
        if exc and exc not in message:
            message = f"{message} | exception: {exc}"
        message = message[:LOG_MAX_MESSAGE_CHARS]
        number = src.get("severity", {}).get("number") or 0
        group = groups.setdefault((svc, message), {
            "service": svc, "severity": severity_label(number),
            "severity_number": number, "count": 0, "message": message,
        })
        group["count"] += 1
        group["severity_number"] = max(group["severity_number"], number)

    ordered = sorted(groups.values(), key=lambda g: (-g["severity_number"], -g["count"]))
    for g in ordered:
        g["severity"] = severity_label(g.pop("severity_number"))
    return {
        "window_minutes": minutes,
        "matching_logs": hits["total"]["value"],
        "logs_scanned": len(hits["hits"]),
        "groups_total": len(ordered),
        "groups": ordered[:LOG_MAX_GROUPS],
    }


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
