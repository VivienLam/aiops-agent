import math
import logging
import re

import httpx
from mcp.server.mcpserver import MCPServer

PROMETHEUS_URL = "http://localhost:9090"
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
