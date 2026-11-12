import math
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

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

# Jaeger tools limits
JAEGER_URL = "http://localhost:8080/jaeger/ui/api/v3"
TRACE_MAX_RESULTS = 10
TRACE_SEARCH_DEPTH = 50  # traces fetched from Jaeger before sorting
TRACE_MAX_MINUTES = 60
TRACE_MAX_TREE_LINES = 40
TRACE_MIN_SHOWN_FRACTION = 0.05  # off-path spans shorter than 5% of the trace are hidden
TRACE_MAX_ATTR_CHARS = 200
TRACE_OVERRUN_MIN_FRACTION = 0.05  # child ending later than this past its parent: flagged
TRACE_ID = re.compile(r"^[0-9a-f]{16,32}$")
STATUS_ERROR = 2  # OTLP status code
NO_MESSAGE = "error (no message)"

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


def otlp_value(value: dict):
    """Unwrap an OTLP attribute value ({"stringValue": "x"} -> "x")."""
    return next(iter(value.values()), None) if value else None


def cut(text, limit: int = TRACE_MAX_ATTR_CHARS) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "..."


OUTGOING_KINDS = (3, 4, "SPAN_KIND_CLIENT", "SPAN_KIND_PRODUCER")
IP_ADDRESS = re.compile(r"^[\d.]+$|^[0-9a-fA-F:]*:[0-9a-fA-F:]+$")


def call_target(kind, attrs: dict) -> str | None:
    """Service or host an outgoing span calls (client/producer spans only)."""
    if kind not in OUTGOING_KINDS:
        return None
    target = attrs.get("peer.service") or attrs.get("server.address") or attrs.get("net.peer.name")
    if not target:
        url = attrs.get("url.full") or attrs.get("http.url")
        if url:
            target = urlsplit(str(url)).hostname
    return cut(target, 60) if target else None


def parse_spans(otlp: dict) -> dict[str, list[dict]]:
    """Flatten Jaeger's OTLP JSON into {trace_id: [span, ...]} with simple fields."""
    traces: dict[str, list[dict]] = {}
    for rs in otlp.get("result", {}).get("resourceSpans", []):
        res = {a["key"]: otlp_value(a["value"]) for a in rs.get("resource", {}).get("attributes", [])}
        service = res.get("service.name", "unknown")
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                attrs = {a["key"]: otlp_value(a["value"]) for a in sp.get("attributes", [])}
                status = sp.get("status", {})
                error = None
                if status.get("code") in (STATUS_ERROR, "STATUS_CODE_ERROR"):
                    error = status.get("message") or ""
                    for ev in sp.get("events", []):
                        if ev.get("name") == "exception":
                            ev_attrs = {a["key"]: otlp_value(a["value"]) for a in ev.get("attributes", [])}
                            error = ev_attrs.get("exception.message") or error
                    error = cut(error) if error else NO_MESSAGE
                traces.setdefault(sp["traceId"], []).append({
                    "id": sp["spanId"],
                    "parent": sp.get("parentSpanId") or None,
                    "service": service,
                    "name": sp["name"],
                    "start": int(sp["startTimeUnixNano"]),
                    "end": int(sp["endTimeUnixNano"]),
                    "error": error,
                    "url": attrs.get("http.route") or attrs.get("url.path")
                           or attrs.get("http.target") or attrs.get("http.url"),
                    "target": call_target(sp.get("kind"), attrs),
                })
    return traces


def ms(nanos: int) -> float:
    value = nanos / 1e6
    return round(value, 1) if value < 10 else round(value)


def find_roots(spans: list[dict]) -> list[dict]:
    """Spans without a parent in this trace (several if the trace is incomplete)."""
    ids = {s["id"] for s in spans}
    return sorted((s for s in spans if s["parent"] not in ids), key=lambda s: s["start"])


def endpoint(span: dict) -> str:
    label = f'{span["service"]} → {span["name"]}'
    if span["url"] and span["url"] not in span["name"]:
        label += f' ({cut(span["url"], 100)})'
    return label


@mcp.tool()
def find_traces(service: str, minutes: int = 15, only_errors: bool = False,
                min_duration_ms: int = 0) -> dict:
    """Find recent traces that go through a service, slowest first (max 10).

    Options: only_errors keeps traces where this service has an error span,
    min_duration_ms keeps slow ones. Each trace gives its id, root endpoint,
    total duration, span count, services crossed and error spans with their
    message. Use get_trace with an id to see the call tree.
    """
    if not SERVICE_NAME.match(service):
        return {"error": f"invalid service name: {service!r}"}
    minutes = max(1, min(minutes, TRACE_MAX_MINUTES))
    min_duration_ms = max(0, min(min_duration_ms, 600_000))
    now = datetime.now(timezone.utc)
    params = {
        "query.service_name": service,
        "query.start_time_min": (now - timedelta(minutes=minutes)).isoformat(),
        "query.start_time_max": now.isoformat(),
        "query.search_depth": TRACE_SEARCH_DEPTH,
    }
    if min_duration_ms:
        params["query.duration_min"] = f"{min_duration_ms}ms"
    if only_errors:
        params["query.attributes"] = '{"error":"true"}'
    resp = httpx.get(f"{JAEGER_URL}/traces", params=params, timeout=20)
    if resp.status_code == 404:  # Jaeger answers 404 when nothing matches
        return {"service": service, "window_minutes": minutes, "traces": []}
    resp.raise_for_status()

    summaries = []
    for trace_id, spans in parse_spans(resp.json()).items():
        start = min(s["start"] for s in spans)
        end = max(s["end"] for s in spans)
        summaries.append({
            "trace_id": trace_id,
            "root": endpoint(find_roots(spans)[0]),
            "duration_ms": ms(end - start),
            "spans": len(spans),
            "services": sorted({s["service"] for s in spans}),
            # Spans with a real message first, then the ones without.
            "errors": [f'{s["service"]} → {s["name"]}: {s["error"]}'
                       for s in sorted((s for s in spans if s["error"]),
                                       key=lambda s: s["error"] == NO_MESSAGE)][:5],
        })
    summaries.sort(key=lambda t: -t["duration_ms"])
    return {
        "service": service,
        "window_minutes": minutes,
        "traces_found": len(summaries),
        "traces": summaries[:TRACE_MAX_RESULTS],
    }


def clipped(child: dict, parent: dict) -> tuple[int, int]:
    """Child interval cut to the parent's window (a caller that gave up, or clock skew)."""
    return max(child["start"], parent["start"]), min(child["end"], parent["end"])


def self_time_of(span: dict, children: dict[str, list[dict]]) -> int:
    """Span duration minus the union of its children's intervals (clipped to the span)."""
    intervals = sorted(clipped(c, span) for c in children.get(span["id"], []))
    covered, cur_start, cur_end = 0, None, None
    for start, end in intervals:
        if end <= start:
            continue  # entirely outside the parent's window
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                covered += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        covered += cur_end - cur_start
    return span["end"] - span["start"] - covered


def critical_path(span: dict, children: dict[str, list[dict]], path: set[str]) -> None:
    """Walk back from the end of `span`: the child that ends last (clipped to the
    span) is what the span was waiting for. Then the same inside that child."""
    path.add(span["id"])
    t = span["end"]
    kids = sorted(children.get(span["id"], []), key=lambda c: clipped(c, span)[1], reverse=True)
    for child in kids:
        start, end = clipped(child, span)
        # Skip a child only if it starts after the point reached: one that ends a bit
        # later (calls overlapping by a fraction of a ms) is still what we waited for.
        if end <= start or start >= t:
            continue  # outside the window, or entirely after the path found
        critical_path(child, children, path)
        t = start
        if t <= span["start"]:
            break


def overrun_note(span: dict, parent: dict | None) -> str | None:
    """When a callee keeps running after its caller stopped waiting."""
    if parent is None:
        return None
    parent_dur = parent["end"] - parent["start"]
    if span["end"] - parent["end"] <= TRACE_OVERRUN_MIN_FRACTION * parent_dur:
        return None  # small differences are clock skew between services
    return (f"caller gave up after {parent_dur / 1e9:.1f} s, "
            f"callee ran {(span['end'] - span['start']) / 1e9:.1f} s")


@mcp.tool()
def get_trace(trace_id: str) -> dict:
    """Call tree of one trace: service → operation, duration, error.

    Spans marked * are on the critical path (what the total duration waits for).
    A long span that only waits for a child is not slow itself: look at
    "slowest_on_critical_path", the spans whose own time explains the duration.
    """
    if not TRACE_ID.match(trace_id):
        return {"error": "invalid trace_id: hexadecimal only, 16 to 32 characters"}
    resp = httpx.get(f"{JAEGER_URL}/traces/{trace_id}", timeout=20)
    if resp.status_code == 404:
        return {"error": f"trace {trace_id} not found"}
    resp.raise_for_status()
    spans = parse_spans(resp.json()).get(trace_id)
    if not spans:
        return {"error": f"trace {trace_id} not found"}

    children: dict[str, list[dict]] = {}
    for s in spans:
        if s["parent"]:
            children.setdefault(s["parent"], []).append(s)
    # A bare IP as target says little: use the called service when its span is here.
    for s in spans:
        if s["target"] and IP_ADDRESS.match(s["target"]):
            callee = next((k["service"] for k in children.get(s["id"], [])
                           if k["service"] != s["service"]), None)
            s["target"] = callee or s["target"]
    roots = find_roots(spans)
    by_id = {s["id"]: s for s in spans}
    on_path: set[str] = set()
    for root in roots:
        critical_path(root, children, on_path)
    self_time = {i: self_time_of(by_id[i], children) for i in on_path}

    start = min(s["start"] for s in spans)
    end = max(s["end"] for s in spans)
    min_shown = (end - start) * TRACE_MIN_SHOWN_FRACTION

    # A span is shown if it is in error, long enough, or has a shown descendant.
    shown: dict[str, bool] = {}

    def is_shown(s: dict) -> bool:
        if s["id"] not in shown:
            kids = [is_shown(k) for k in children.get(s["id"], [])]  # visit all
            shown[s["id"]] = (bool(s["error"]) or s["end"] - s["start"] >= min_shown
                              or any(kids))
        return shown[s["id"]]

    def child_groups(group: list[dict]) -> list[list[dict]]:
        """Children with identical siblings (same service, operation and call target)
        merged. A group is kept if one span is shown or if together they are long enough."""
        return [g for g in all_child_groups(group) if keep_group(g)]

    def all_child_groups(group: list[dict]) -> list[list[dict]]:
        groups: dict[tuple, list[dict]] = {}
        kids = [k for s in group for k in children.get(s["id"], [])]
        for k in sorted(kids, key=lambda k: k["start"]):
            groups.setdefault((k["service"], k["name"], k["target"]), []).append(k)
        return list(groups.values())

    def keep_group(g: list[dict]) -> bool:
        return (any(is_shown(k) for k in g)
                or sum(k["end"] - k["start"] for k in g) >= min_shown)

    def hidden_calls(group: list[dict]) -> str:
        """Other services called by hidden children, so a hop is never lost."""
        calls = {}
        for g in all_child_groups(group):
            if not keep_group(g) and g[0]["service"] != group[0]["service"]:
                d = max(k["end"] - k["start"] for k in g)
                calls[g[0]["service"]] = max(calls.get(g[0]["service"], 0), d)
        return ", ".join(f"{svc} {ms(d)} ms" for svc, d in list(calls.items())[:3])

    lines: list[str] = []
    hidden_lines = 0
    rendered = 0

    def render(group: list[dict], depth: int) -> None:
        nonlocal hidden_lines, rendered
        rendered += len(group)
        first = group[0]
        names = [first["name"]]
        on_path = any(s["id"] in self_time for s in group)
        errors = [s["error"] for s in group if s["error"]]
        notes = [n for s in group if (n := overrun_note(s, by_id.get(s["parent"])))]
        dur = max(s["end"] - s["start"] for s in group)
        kids = child_groups(group)
        # Same-service pass-through spans (only child, ~same duration) go on one line.
        while (len(group) == 1 and len(kids) == 1 and len(kids[0]) == 1
               and kids[0][0]["service"] == first["service"]
               and kids[0][0]["end"] - kids[0][0]["start"] >= 0.9 * dur):
            group = kids[0]
            rendered += 1
            names.append(group[0]["name"])
            errors += [group[0]["error"]] if group[0]["error"] else []
            notes += [n for n in [overrun_note(group[0], by_id.get(group[0]["parent"]))] if n]
            kids = child_groups(group)
        target = group[0]["target"]
        line = f'{"  " * depth}{"*" if on_path else "-"} {first["service"]} → {" › ".join(names)}'
        if target and target != first["service"]:
            line += f" (to {target})"
        line += f" ×{len(group)} (max {ms(dur)} ms)" if len(group) > 1 else f" {ms(dur)} ms"
        if notes:
            line += f" ({notes[0]})"
        calls = hidden_calls(group)
        if calls:
            line += f" [calls {calls}]"
        if errors:
            line += f" ERR: {max(errors, key=lambda e: e != NO_MESSAGE)}"
        if len(lines) >= TRACE_MAX_TREE_LINES:
            hidden_lines += 1
        else:
            lines.append(line)
        for g in kids:
            render(g, depth + 1)

    for root in roots:
        render([root], 0)

    slowest = sorted(self_time.items(), key=lambda kv: -kv[1])[:3]
    result = {
        "trace_id": trace_id,
        "duration_ms": ms(end - start),
        "spans": len(spans),
        "slowest_on_critical_path": [
            f'{by_id[i]["service"]} → {by_id[i]["name"]}'
            f'{f" (to {by_id[i]["target"]})" if by_id[i]["target"] else ""}: {ms(t)} ms of its own time'
            for i, t in slowest if t > 0
        ],
        "tree": "\n".join(lines),
        "short_spans_hidden": len(spans) - rendered,
    }
    if hidden_lines:
        result["tree"] += f"\n... {hidden_lines} more lines not shown"
    if len(roots) > 1:
        result["note"] = f"{len(roots)} root spans: some parent spans are missing"
    return result


if __name__ == "__main__":
    mcp.run()
