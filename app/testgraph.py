import os, pathlib, tempfile
os.environ["LLM_PROVIDER"] = "fake"
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "r.db")
os.environ["CHECKPOINT_PATH"] = os.path.join(tmp, "c.db")

from langgraph.types import Command
from app.graph import GRAPH
from app import store

SAMPLES = pathlib.Path(__file__).parent.parent / "sample_reports"

def cfg(i): return {"configurable": {"thread_id": i}}

def test_good_report_waits_for_approval_then_saves():
    text = (SAMPLES / "good_canada_q3.txt").read_text()
    out = GRAPH.invoke({"report_id": "g1", "text": text, "repairs": 0}, cfg("g1"))
    assert "__interrupt__" in out                       # paused for a person
    assert out["fields"]["country"] == "Canada"          # repaired from "canada"
    assert out["fields"]["quarter"] == "Q3"              # repaired from "3"
    done = GRAPH.invoke(Command(resume={"approved": True, "reviewer": "Ana"}), cfg("g1"))
    assert done["status"] == "saved" and "142 participants" in done["summary"]
    assert store.CON.execute("select count(*) from approved_reports where id='g1'").fetchone()[0] == 1

def test_missing_email_is_returned_to_sender():
    text = (SAMPLES / "missing_email_nz.txt").read_text()
    out = GRAPH.invoke({"report_id": "m1", "text": text, "repairs": 0}, cfg("m1"))
    assert out["status"] == "returned" and "contact_email is missing" in out["fix_request"]
    assert out["repairs"] == 0                            # a missing value goes straight back to the sender

def test_rejected_report_is_not_saved():
    text = (SAMPLES / "good_canada_q3.txt").read_text()
    GRAPH.invoke({"report_id": "r1", "text": text, "repairs": 0}, cfg("r1"))
    done = GRAPH.invoke(Command(resume={"approved": False, "reviewer": "Ana", "note": "duplicate"}), cfg("r1"))
    assert done["status"] == "rejected"
    assert store.CON.execute("select count(*) from approved_reports where id='r1'").fetchone()[0] == 0

def test_email_report_gets_reply_in_thread(monkeypatch):
    from app import gmail
    sent = []
    monkeypatch.setattr(gmail, "reply_fix_request", lambda email, msg: sent.append((email["thread_id"], msg)))
    email = {"from": "ops@example.org", "subject": "Quarterly report", "thread_id": "t1", "message_id": "<m1>"}
    text = (SAMPLES / "missing_email_nz.txt").read_text()
    out = GRAPH.invoke({"report_id": "e1", "text": text, "repairs": 0, "email": email}, cfg("e1"))
    assert out["status"] == "returned" and sent and sent[0][0] == "t1"