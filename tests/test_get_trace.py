"""get_trace on a hand-made trace that reproduces a real checkout failure.

The proxy gives up after 15 s, the frontend span ends 2 ms after it (clock skew),
the frontend's call to checkout stops at 15 s while checkout runs 17.1 s.
Inside checkout: a 6.9 s step, then two POSTs to two different targets
(shipping, email), then a publish.
"""
import server

MS = 1_000_000  # nanoseconds
TRACE_ID = "ab" * 16


def span(sid, parent, name, start_ms, end_ms, kind=2, attrs=None, error=None):
    sp = {
        "traceId": TRACE_ID, "spanId": sid, "name": name, "kind": kind,
        "startTimeUnixNano": str(start_ms * MS), "endTimeUnixNano": str(end_ms * MS),
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in (attrs or {}).items()],
        "status": {"code": 2, "message": error} if error else {},
    }
    if parent:
        sp["parentSpanId"] = parent
    return sp


def otlp(spans_by_service):
    return {"result": {"resourceSpans": [
        {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": svc}}]},
         "scopeSpans": [{"spans": spans}]}
        for svc, spans in spans_by_service.items()]}}


CLIENT = 3
FAKE = otlp({
    "load-generator": [span("a1", None, "user_checkout_single", 0, 15031, kind=1)],
    "frontend-proxy": [span("b1", "a1", "router frontend egress", 10, 15009, kind=CLIENT)],
    "frontend": [
        span("c1", "b1", "POST /api/checkout", 12, 15011),
        span("c2", "c1", "oteldemo.CheckoutService/PlaceOrder", 14, 15011, kind=CLIENT),
    ],
    "checkout": [
        span("d1", "c2", "oteldemo.CheckoutService/PlaceOrder", 20, 17112),
        span("d2", "d1", "prepareOrderItemsAndShippingQuoteFromCart", 30, 6922, kind=1),
        span("d3", "d1", "POST", 6930, 9610, kind=CLIENT,
             attrs={"server.address": "shipping", "url.full": "http://shipping:50050/ship-order"}),
        span("d4", "d1", "POST", 9620, 12300, kind=CLIENT, error="context deadline exceeded",
             attrs={"server.address": "email",
                    "url.full": "http://email:6060/send_order_confirmation"}),
        span("d5", "d1", "publish orders", 12310, 13490, kind=4),
    ],
})


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return FAKE


def fake_trace(monkeypatch):
    monkeypatch.setattr(server.httpx, "get", lambda *a, **k: FakeResponse())
    return server.get_trace(TRACE_ID)


def test_frontend_is_not_blamed_for_waiting(monkeypatch):
    result = fake_trace(monkeypatch)
    slowest = result["slowest_on_critical_path"]
    assert not any(line.startswith("frontend") for line in slowest), slowest
    assert slowest[0].startswith("checkout → prepareOrderItemsAndShippingQuoteFromCart"), slowest


def test_self_time_is_duration_minus_children():
    spans = server.parse_spans(FAKE)[TRACE_ID]
    children = {}
    for s in spans:
        if s["parent"]:
            children.setdefault(s["parent"], []).append(s)
    by_id = {s["id"]: s for s in spans}
    # checkout server span: 17092 ms minus its children (6892 + 2680 + 2680 + 1180)
    assert server.self_time_of(by_id["d1"], children) == (17092 - 13432) * MS
    # frontend span: its only child starts 2 ms in and covers the rest
    assert server.self_time_of(by_id["c1"], children) == 2 * MS
    # child longer than parent: clipped to the parent's window, never negative
    assert server.self_time_of(by_id["c2"], children) == 6 * MS


def test_callee_outliving_caller_is_flagged(monkeypatch):
    tree = fake_trace(monkeypatch)["tree"]
    line = next(l for l in tree.splitlines() if "checkout → oteldemo.CheckoutService/PlaceOrder" in l)
    assert "caller gave up after 15.0 s, callee ran 17.1 s" in line, tree
    # 2 ms of clock skew between proxy and frontend is not flagged
    assert tree.count("caller gave up") == 1, tree


def test_calls_to_different_targets_are_not_merged(monkeypatch):
    tree = fake_trace(monkeypatch)["tree"]
    assert "×2" not in tree, tree
    email = next(l for l in tree.splitlines() if "(to email)" in l)
    assert "context deadline exceeded" in email
    shipping = next(l for l in tree.splitlines() if "(to shipping)" in l)
    assert "ERR" not in shipping


def test_small_overlap_does_not_drop_the_long_call():
    """Real case: the next calls start 0.2 ms before PlaceOrder ends. Walking back
    from the end, PlaceOrder must stay on the critical path, not be seen as parallel."""
    us = 1000  # nanoseconds per microsecond
    route = {"id": "r", "parent": None, "service": "frontend", "name": "route",
             "start": 0, "end": 13_425_000 * us, "error": None, "url": None, "target": None}

    def call(sid, name, start_us, end_us):
        return {"id": sid, "parent": "r", "service": "frontend", "name": name,
                "start": start_us * us, "end": end_us * us, "error": None, "url": None,
                "target": None}

    place_order = call("p", "PlaceOrder", 1_000, 13_406_900)
    products = [call(f"g{i}", "GetProduct", 13_406_700, end)
                for i, end in enumerate([13_412_000, 13_412_000, 13_423_900])]
    children = {"r": [place_order, *products]}
    path: set[str] = set()
    server.critical_path(route, children, path)
    assert "p" in path
    assert "g2" in path  # the one that finishes last
