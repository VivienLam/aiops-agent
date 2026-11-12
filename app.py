"""Web UI: uv run streamlit run app.py

Runs the agent and shows each tool call live, then the report.
Past runs are read back from runs/ (the run logs are the only source of truth).
"""

import asyncio
import json
import queue
import threading
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from agent.graph import RUNS_DIR, DailyQuotaExhausted, RunLog, load_model
from agent.runner import run_agent

load_dotenv(Path(__file__).resolve().parent / ".env")

DEFAULT_QUESTION = "Users report the shop feels broken. Investigate."
STATUS_COLOR = {"incident": "red", "no_incident": "green", "uncertain": "orange"}

st.set_page_config(page_title="AIOps agent", layout="wide")


# ---------- reading run logs ----------

def run_files() -> list[Path]:
    return sorted(RUNS_DIR.glob("*.json"), reverse=True) if RUNS_DIR.exists() else []


def load_run(path: Path) -> dict:
    data = json.loads(path.read_text())
    n = 0
    for step in data.get("steps", []):
        for call in step["tool_calls"]:
            n += 1
            call.setdefault("call", n)  # logs older than step 2.3 have no number
    return data


def run_label(path: Path) -> str:
    try:
        when = datetime.strptime(path.stem, "%Y%m%d-%H%M%S").strftime("%d/%m %H:%M:%S")
    except ValueError:
        when = path.stem
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return f"{when} · unreadable"
    report = data.get("report")
    status = report["status"] if report else ("error" if data.get("error") else "no report")
    return f"{when} · {status}"


# ---------- rendering ----------

def show_tool_call(call: dict) -> None:
    args = json.dumps(call["args"], ensure_ascii=False)
    skipped = "" if call.get("executed", True) else "  · skipped (budget reached)"
    title = f"#{call['call']}  {call['tool']}  {args}  —  {call['output_chars']} chars{skipped}"
    with st.expander(title):
        st.code(call["output"], language=None, wrap_lines=True)


def fmt(value, unit: str = "") -> str:
    return "—" if value is None else f"{value}{unit}"


def show_report(data: dict) -> None:
    report, checks = data.get("report"), data.get("checks") or {}
    if data.get("error"):
        st.error(data["error"])
    if not report:
        st.info("No structured report in this run (older log, or the report failed).")
        if data.get("answer"):
            st.markdown(data["answer"])
        return

    color = STATUS_COLOR.get(report["status"], "gray")
    st.markdown(f"## :{color}-background[{report['status'].upper()}]")

    root = checks.get("root_cause_service") or {}
    root_mark = {True: " :green[✔]", False: " :red[✘ not in list_services]"}.get(root.get("ok"), "")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Root cause service**  \n{fmt(report['root_cause_service'])}{root_mark}")
    c2.markdown(f"**Fault type**  \n{fmt(report['fault_type'])}")
    c3.markdown(f"**Confidence**  \n{report['confidence']:.2f}")
    c3.progress(report["confidence"])

    impact = report["impact"]
    st.markdown("**Impact**")
    i1, i2, i3, i4 = st.columns(4)
    i1.metric("Error rate", fmt(impact.get("error_rate_percent"), " %"))
    i2.metric("Latency", fmt(impact.get("latency_ms"), " ms"))
    i3.metric("Errors counted", fmt(impact.get("error_count")))
    i4.metric("Window", fmt(impact.get("window_minutes"), " min"))

    ev_checks = checks.get("evidence") or {}
    details = ev_checks.get("details", [])
    head = "**Evidence**"
    if ev_checks:
        head += f" — {ev_checks['verified']}/{ev_checks['total']} verified"
    st.markdown(head)
    for i, ev in enumerate(report["evidence"]):
        detail = details[i] if i < len(details) else None
        mark = ":gray[?]" if detail is None else (":green[✔]" if detail["ok"] else ":red[✘]")
        reason = "" if detail is None or detail["ok"] else f" :red[({detail['reason']})]"
        st.markdown(f"{mark} tool call **#{ev['step']}**{reason}")
        st.code(ev["quote"], language=None, wrap_lines=True)

    st.markdown(f"**Proposed action**  \n{report['proposed_action']}")
    st.markdown(f"**Missing info**  \n{report['missing_info']}")


def show_totals(data: dict) -> None:
    t = data.get("totals") or {}
    if not t:
        return
    st.markdown("**Totals**")
    a, b, c, c2, d, e = st.columns(6)
    a.metric("LLM calls", t["llm_calls"])
    b.metric("Tool calls", t["tool_calls"])
    c.metric("Tokens in", f"{t['input_tokens']:,}")
    c2.metric("Tokens out", f"{t['output_tokens']:,}")
    d.metric("Duration", f"{t['duration_s']} s")
    e.metric("Without waits", f"{t.get('active_s', t['duration_s'])} s",
             help=f"Waited {t.get('waited_s', 0)} s on rate limits or overload")


def show_run(data: dict) -> None:
    st.caption(f"{data.get('started_at', '')} · {data.get('model', '')}")
    st.markdown(f"**Question:** {data.get('question', '')}")
    calls = [c for s in data.get("steps", []) for c in s["tool_calls"]]
    st.markdown(f"**Tool calls ({len(calls)})**")
    for call in calls:
        show_tool_call(call)
    st.divider()
    show_report(data)
    st.divider()
    show_totals(data)


# ---------- live run ----------
# The agent runs in its own thread with its own event loop, and sends events through
# a queue. Streamlit only reads the queue. The run goes to the end and saves its log
# even if the browser tab is closed.

def start_run(question: str) -> queue.Queue:
    events: queue.Queue = queue.Queue()

    def worker():
        model_name, llm = load_model()
        log = RunLog(question, model_name)

        async def consume():
            async for event in run_agent(question, llm, log):
                events.put(event)

        try:
            asyncio.run(consume())
        except* DailyQuotaExhausted as group:
            log.error = f"daily quota exhausted: {group.exceptions[0]}"
        except* Exception as group:
            log.error = f"run failed: {group.exceptions[0]!r}"
        finally:
            events.put({"type": "saved", "path": log.save()})

    threading.Thread(target=worker, daemon=True).start()
    return events


# ---------- page ----------

files = run_files()
if "pending_select" in st.session_state:
    st.session_state["selected"] = st.session_state.pop("pending_select")

with st.sidebar:
    st.header("Past runs")
    selected = st.radio("Run logs", [p.name for p in files], key="selected",
                        format_func=lambda name: run_label(RUNS_DIR / name),
                        label_visibility="collapsed") if files else None

st.title("AIOps agent")
question = st.text_area("Question", DEFAULT_QUESTION, height=80)
start = st.button("Lancer", type="primary", disabled=not question.strip())

# Two slots: `live` is empty except during a run, `view` shows a saved run.
# Leaving `view` empty during a run clears the previous run at once; reusing the same
# slot would keep its old elements on screen (greyed out) until the run ends.
live, view = st.empty(), st.empty()
if start:
    with live.container():
        with st.status("Investigating…", expanded=True) as status:
            events = start_run(question.strip())
            while (event := events.get())["type"] != "saved":
                if event["type"] == "tool_call":
                    show_tool_call(event)
            path = event["path"]
            status.update(label=f"Done — {path.name}", state="complete", expanded=False)
    # Rerun once so the sidebar selects the new log; the page then shows it from the file.
    st.session_state["pending_select"] = path.name
    st.rerun()
elif selected:
    with view.container():
        show_run(load_run(RUNS_DIR / selected))
