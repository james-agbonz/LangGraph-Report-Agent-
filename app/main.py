"""Web app: submit a report, see what the agent did, approve or reject what is waiting."""
import uuid

from fastapi import BackgroundTasks, FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Template
from langgraph.types import Command

from . import config, store
from .graph import GRAPH

app = FastAPI(title="Report Intake Agent")
FINAL = {"pending_approval", "saved", "returned", "rejected", "failed"}


@app.on_event("startup")
def mark_interrupted_runs():
    """A run still 'in progress' when the server starts was cut off by a restart: say so instead of leaving it hanging."""
    for s in store.list_submissions():
        if s["status"] not in FINAL:
            store.upsert_submission(s["id"], status="failed", errors=["interrupted: the server restarted mid-run, submit it again"])


def run(report_id: str, payload):
    """Run or resume the graph; a model failure is recorded on the report instead of crashing the page."""
    try:
        return GRAPH.invoke(payload, {"configurable": {"thread_id": report_id}})
    except Exception as e:
        store.upsert_submission(report_id, status="failed", errors=[f"{type(e).__name__}: {e}"])
        return None


@app.post("/reports")
def submit(background: BackgroundTasks, text: str = Form(...)):
    report_id = uuid.uuid4().hex[:8]
    store.upsert_submission(report_id, status="queued")
    background.add_task(run, report_id, {"report_id": report_id, "text": text, "repairs": 0})
    return RedirectResponse("/", status_code=303)


@app.post("/reports/{report_id}/decision")
def decide(report_id: str, background: BackgroundTasks, approved: str = Form(...), reviewer: str = Form("Manager"), note: str = Form("")):
    store.upsert_submission(report_id, status="approved, finishing" if approved == "yes" else "rejecting")
    background.add_task(run, report_id, Command(resume={"approved": approved == "yes", "reviewer": reviewer, "note": note}))
    return RedirectResponse("/", status_code=303)


@app.get("/api/submissions")
def api_submissions():
    return store.list_submissions()


PAGE = Template("""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Report Intake Agent</title>{% if busy %}<meta http-equiv="refresh" content="3">{% endif %}<style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1b1b1b;background:#fafafa}
h1{font-size:22px;margin:0 0 4px} .muted{color:#666;font-size:13px} textarea{width:100%;height:150px;font:13px monospace}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#e3f2fd}
.pending_approval{background:#fff3cd}.saved{background:#d4edda}.returned,.rejected,.failed{background:#f8d7da}
table{border-collapse:collapse;font-size:13px}td{padding:2px 10px 2px 0;vertical-align:top}
button{padding:6px 14px;border-radius:6px;border:1px solid #888;cursor:pointer}.ok{background:#2e7d32;color:#fff;border:0}.no{background:#c62828;color:#fff;border:0}
</style></head><body>
<h1>Report Intake Agent</h1><div class="muted">LangGraph agent · model: {{ model }} · pending approvals: {{ pending|length }}{% if busy %} · working…{% endif %}</div>
<div class="card"><form method="post" action="/reports"><b>Submit a quarterly report</b><br><textarea name="text" placeholder="Paste the report text"></textarea><br><button>Submit</button></form></div>
{% for s in subs %}<div class="card"><span class="tag {{ s.status }}">{{ s.status.replace('_',' ') }}</span> <b>#{{ s.id }}</b> <span class="muted">{{ s.created_at }}{% if s.source %} · {{ s.source }}{% endif %}</span>
<table>{% for k,v in s.fields.items() %}<tr><td class="muted">{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>
{% if s.errors %}<p><b>Errors:</b> {{ s.errors|join('; ') }}</p>{% endif %}
{% if s.fix_request %}<p><b>Message drafted to sender:</b> {{ s.fix_request }}</p>{% endif %}
{% if s.summary %}<p><b>Leadership summary:</b> {{ s.summary }}</p>{% endif %}
{% if s.reviewer %}<p class="muted">Decided by {{ s.reviewer }}</p>{% endif %}{% if s.note %}<p class="muted">{{ s.note }}</p>{% endif %}
{% if s.status == 'pending_approval' %}<form method="post" action="/reports/{{ s.id }}/decision">
<input name="reviewer" value="Manager"> <input name="note" placeholder="Note (optional)">
<button class="ok" name="approved" value="yes">Approve</button> <button class="no" name="approved" value="no">Reject</button></form>{% endif %}
</div>{% endfor %}</body></html>""")


@app.get("/", response_class=HTMLResponse)
def home():
    subs = store.list_submissions()
    model = "rule-based demo" if config.LLM_PROVIDER == "fake" else \
        f"{config.LLM_MODEL} @ {config.LLM_BASE_URL} · thinking {'off' if config.LLM_NO_THINK else 'on'}"
    busy = any(s["status"] not in FINAL for s in subs)  # refresh every 3 s while the agent is working
    return PAGE.render(subs=subs, pending=[s for s in subs if s["status"] == "pending_approval"], model=model, busy=busy)