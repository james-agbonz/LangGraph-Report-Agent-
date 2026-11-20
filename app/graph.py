"""The report intake agent as a LangGraph graph.

extract -> validate --ok--------------------------> approval (pauses for a person) --approved--> save -> summarize
                    --errors, repairs left-------> repair -> validate (loop)          --rejected--> end
                    --errors, no repairs left----> return_to_sender -> end
"""
import re
import sqlite3
from typing import Optional, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import config, store
from .llm import get_llm

LLM = get_llm()
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


class ReportState(TypedDict, total=False):
    report_id: str
    text: str
    fields: dict
    errors: list[str]
    repairs: int
    status: str
    fix_request: str
    reviewer: str
    note: str
    summary: str
    email: dict  # set when the report came in by Gmail


def extract(state: ReportState):
    store.upsert_submission(state["report_id"], status="extracting")
    fields = LLM.extract(state["text"])
    store.upsert_submission(state["report_id"], status="extracted", fields=fields)
    return {"fields": fields, "repairs": state.get("repairs", 0)}


COUNTRY_ALIASES = {"us": "US", "usa": "US", "united states": "US", "canada": "Canada", "ca": "Canada",
                   "nz": "New Zealand", "new zealand": "New Zealand"}


def normalize(fields: dict) -> dict:
    """Fix formatting in code (case, 'Q3' vs '3', '$48,500'), so the model only handles real gaps."""
    f = dict(fields)
    if isinstance(f.get("country"), str):
        f["country"] = COUNTRY_ALIASES.get(f["country"].strip().lower(), f["country"].strip())
    q = str(f.get("quarter") or "").upper().replace(" ", "").replace("QUARTER", "Q")
    if re.fullmatch(r"Q?[1-4]", q):
        f["quarter"] = "Q" + q[-1]
    for name, cast in (("year", int), ("participants", int), ("budget_spent", float)):
        if isinstance(f.get(name), str):
            digits = re.sub(r"[^\d.]", "", f[name])
            f[name] = cast(float(digits)) if digits else None
    if isinstance(f.get("contact_email"), str):
        f["contact_email"] = f["contact_email"].strip() or None
    return f


def validate(state: ReportState):
    f, errors = normalize(state["fields"]), []
    for name in ("organization", "country", "quarter", "year", "contact_email", "participants", "budget_spent"):
        if f.get(name) in (None, ""):
            errors.append(f"{name} is missing")
    if f.get("country") and f["country"] not in config.VALID_COUNTRIES:
        errors.append(f"country must be one of {', '.join(config.VALID_COUNTRIES)} (got {f['country']})")
    if f.get("quarter") and not re.fullmatch(r"Q[1-4]", str(f["quarter"])):
        errors.append(f"quarter must be Q1 to Q4 (got {f['quarter']})")
    if f.get("year") and not 2020 <= int(f["year"]) <= 2030:
        errors.append(f"year looks wrong ({f['year']})")
    if f.get("contact_email") and not EMAIL.match(str(f["contact_email"])):
        errors.append(f"contact_email is not a valid address ({f['contact_email']})")
    for name in ("participants", "budget_spent"):
        if isinstance(f.get(name), (int, float)) and f[name] < 0:
            errors.append(f"{name} cannot be negative")
    store.upsert_submission(state["report_id"], status="validated" if not errors else "has_errors", errors=errors, fields=f)
    return {"errors": errors, "fields": f}


def route_after_validate(state: ReportState) -> str:
    if not state["errors"]:
        return "approval"
    # A missing value can only come from the sender, so repairing is pointless: send it back at once.
    fixable = [e for e in state["errors"] if not e.endswith("is missing")]
    if fixable and state.get("repairs", 0) < config.MAX_REPAIRS:
        return "repair"
    return "return_to_sender"


def repair(state: ReportState):
    store.upsert_submission(state["report_id"], status=f"repairing ({state.get('repairs', 0) + 1} of {config.MAX_REPAIRS})")
    fields = LLM.repair(state["text"], state["fields"], state["errors"])
    return {"fields": fields, "repairs": state.get("repairs", 0) + 1}


def return_to_sender(state: ReportState):
    store.upsert_submission(state["report_id"], status="drafting reply")
    message = LLM.fix_request(state["fields"], state["errors"])
    store.upsert_submission(state["report_id"], status="returned", fix_request=message, fields=state["fields"])
    if state.get("email"):
        _gmail("reply_fix_request", state["report_id"], state["email"], message)
    return {"status": "returned", "fix_request": message}


def approval(state: ReportState):
    store.upsert_submission(state["report_id"], status="pending_approval", fields=state["fields"], errors=[])
    # The graph stops here and is saved to the checkpoint database. It resumes when a person decides.
    decision = interrupt({"report_id": state["report_id"], "fields": state["fields"]})
    approved = bool(decision.get("approved"))
    store.upsert_submission(state["report_id"], status="approved" if approved else "rejected",
                            reviewer=decision.get("reviewer", ""), note=decision.get("note", ""))
    return {"status": "approved" if approved else "rejected",
            "reviewer": decision.get("reviewer", ""), "note": decision.get("note", "")}


def route_after_approval(state: ReportState) -> str:
    return "save" if state["status"] == "approved" else END


def save(state: ReportState):
    store.save_approved(state["report_id"], state["fields"], state.get("reviewer", ""))
    return {"status": "saved"}


def summarize(state: ReportState):
    store.upsert_submission(state["report_id"], status="writing summary")
    summary = LLM.summarize(state["fields"])
    store.upsert_submission(state["report_id"], status="saved", summary=summary)
    if state.get("email"):
        _gmail("send_summary", state["report_id"], state["fields"], summary)
    return {"summary": summary, "status": "saved"}


def _gmail(action: str, report_id: str, *args):
    """Email is a side effect: if Gmail fails, the report still moves on and the problem is recorded."""
    try:
        from . import gmail
        getattr(gmail, action)(*args)
        store.upsert_submission(report_id, note=f"Gmail: {action} {'sent' if gmail.SEND else 'saved as draft'}")
    except Exception as e:
        store.upsert_submission(report_id, note=f"Gmail {action} failed: {e}")


def build_graph(checkpointer=None):
    g = StateGraph(ReportState)
    for name, fn in [("extract", extract), ("validate", validate), ("repair", repair),
                     ("return_to_sender", return_to_sender), ("approval", approval),
                     ("save", save), ("summarize", summarize)]:
        g.add_node(name, fn)
    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", route_after_validate, ["approval", "repair", "return_to_sender"])
    g.add_edge("repair", "validate")
    g.add_edge("return_to_sender", END)
    g.add_conditional_edges("approval", route_after_approval, ["save", END])
    g.add_edge("save", "summarize")
    g.add_edge("summarize", END)
    if checkpointer is None:
        import os
        os.makedirs(os.path.dirname(config.CHECKPOINT_PATH) or ".", exist_ok=True)
        checkpointer = SqliteSaver(sqlite3.connect(config.CHECKPOINT_PATH, check_same_thread=False))
    return g.compile(checkpointer=checkpointer)


GRAPH = build_graph()