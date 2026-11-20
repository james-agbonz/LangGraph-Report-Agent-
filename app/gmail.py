"""Gmail in and out.

In:  every POLL_SECONDS, read unread emails with the label GMAIL_LABEL (default "reports"), run each through the agent.
Out: when a report goes back to its sender, write a reply in the same thread; when one is approved,
     write the leadership summary to LEADERSHIP_EMAIL. Replies are saved as Gmail drafts until GMAIL_SEND=true.

One-time setup: see README, "Connect Gmail". Needs credentials.json from Google Cloud next to this project.
Run:  python -m app.gmail
"""
import base64
import os
import time
from email.message import EmailMessage

LABEL = os.getenv("GMAIL_LABEL", "reports")
DONE_LABEL = os.getenv("GMAIL_DONE_LABEL", "reports-processed")
SEND = os.getenv("GMAIL_SEND", "false").lower() == "true"
LEADERSHIP_EMAIL = os.getenv("LEADERSHIP_EMAIL", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]  # read, label, draft and send; never delete

_service = None


def service():
    """Log in once in the browser; the token is kept in token.json (git-ignored)."""
    global _service
    if _service is None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file("token.json", SCOPES) if os.path.exists("token.json") else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES).run_local_server(port=0)
            with open("token.json", "w") as fh:
                fh.write(creds.to_json())
        _service = build("gmail", "v1", credentials=creds)
    return _service


def _label_id(name: str) -> str:
    labels = service().users().labels().list(userId="me").execute().get("labels", [])
    for lab in labels:
        if lab["name"] == name:
            return lab["id"]
    return service().users().labels().create(userId="me", body={"name": name}).execute()["id"]


def _text_of(payload: dict) -> str:
    """Plain-text body plus any .txt attachments."""
    parts, out = [payload], []
    while parts:
        p = parts.pop(0)
        parts.extend(p.get("parts", []))
        mime, name, body = p.get("mimeType", ""), p.get("filename", ""), p.get("body", {})
        if mime == "text/plain" or name.lower().endswith(".txt"):
            data = body.get("data")
            if not data and body.get("attachmentId"):
                data = service().users().messages().attachments().get(
                    userId="me", messageId=p["_msg_id"], id=body["attachmentId"]).execute()["data"]
            if data:
                out.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
    return "\n".join(out).strip()


def _header(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg["payload"].get("headers", []) if h["name"].lower() == name.lower()), "")


def _send_or_draft(to: str, subject: str, body: str, thread_id: str = None, in_reply_to: str = None):
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    if in_reply_to:
        m["In-Reply-To"] = m["References"] = in_reply_to
    m.set_content(body)
    raw = {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}
    if thread_id:
        raw["threadId"] = thread_id
    users = service().users()
    return users.messages().send(userId="me", body=raw).execute() if SEND else \
        users.drafts().create(userId="me", body={"message": raw}).execute()


def reply_fix_request(email: dict, message: str):
    subject = email["subject"] if email["subject"].lower().startswith("re:") else "Re: " + email["subject"]
    return _send_or_draft(email["from"], subject, message, email["thread_id"], email["message_id"])


def send_summary(fields: dict, summary: str):
    if LEADERSHIP_EMAIL:
        subject = f"Approved report: {fields.get('organization')} {fields.get('quarter')} {fields.get('year')}"
        return _send_or_draft(LEADERSHIP_EMAIL, subject, summary)


def poll_once():
    from .main import run  # the same entry point the web page uses
    from . import store
    query = f"label:{LABEL} is:unread -label:{DONE_LABEL}"
    msgs = service().users().messages().list(userId="me", q=query).execute().get("messages", [])
    done = _label_id(DONE_LABEL)
    for ref in msgs:
        msg = service().users().messages().get(userId="me", id=ref["id"], format="full").execute()
        for part in [msg["payload"], *msg["payload"].get("parts", [])]:
            part["_msg_id"] = msg["id"]
        email = {"from": _header(msg, "From"), "subject": _header(msg, "Subject"),
                 "thread_id": msg["threadId"], "message_id": _header(msg, "Message-ID")}
        text = _text_of(msg["payload"])
        report_id = msg["id"][:12]
        store.upsert_submission(report_id, status="submitted", source=f"Gmail: {email['from']}")
        run(report_id, {"report_id": report_id, "text": text, "repairs": 0, "email": email})
        service().users().messages().modify(userId="me", id=msg["id"],
                                            body={"addLabelIds": [done], "removeLabelIds": ["UNREAD"]}).execute()
        print(f"processed {report_id} from {email['from']}")
    return len(msgs)


if __name__ == "__main__":
    print(f"Watching Gmail label '{LABEL}' every {POLL_SECONDS}s; replies are {'sent' if SEND else 'saved as drafts'}.")
    while True:
        try:
            poll_once()
        except Exception as e:
            print("poll error:", e)
        time.sleep(POLL_SECONDS)
