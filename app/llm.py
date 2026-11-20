"""One small LLM wrapper in the OpenAI chat format, plus a rule-based stand-in for tests."""
import json
import re

from openai import OpenAI

from . import config

FIELDS = ["organization", "country", "quarter", "year", "contact_email", "participants", "budget_spent", "highlights"]


def _parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or code fences; take the first {...} block."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"model did not return JSON: {text[:200]}")
    return json.loads(match.group(0))


class OpenAICompatibleLLM:
    def __init__(self):
        self.client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
        self.model = config.LLM_MODEL

    def _chat(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if (json_mode and config.LLM_JSON_MODE) else {}
        if config.LLM_NO_THINK:
            # Thinking models (Qwen 3.x, gpt-oss) can spend 2,000 tokens thinking before a 10-token answer.
            # Ollama turns it off with reasoning_effort "none" (tested on qwen3.5:0.8b: 2,000 tokens down to 6).
            kwargs["extra_body"] = {"reasoning_effort": "none"}
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0, timeout=config.LLM_TIMEOUT, max_tokens=config.LLM_MAX_TOKENS,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)
        choice = resp.choices[0]
        content = choice.message.content or ""
        if not content.strip():
            raise ValueError(f"empty answer (finish_reason={choice.finish_reason}, "
                             f"tokens={resp.usage.completion_tokens if resp.usage else '?'}); "
                             "if tokens are high the model spent them thinking: set LLM_NO_THINK=true")
        return content

    def _json(self, system: str, user: str) -> dict:
        """Ask for JSON; small models sometimes break it, so try twice before giving up."""
        last = None
        for _ in range(2):
            try:
                return _parse_json(self._chat(system, user, json_mode=True))
            except Exception as e:  # bad JSON or a provider without json mode
                last = e
        raise ValueError(f"model did not return usable JSON after 2 tries: {last}")

    def extract(self, text: str) -> dict:
        system = ("You extract fields from a quarterly program report. Reply with JSON only, keys: "
                  + ", ".join(FIELDS) + ". Use null for anything the report does not state. "
                  "year, participants and budget_spent are numbers; highlights is one sentence.")
        return self._json(system, text)

    def repair(self, text: str, fields: dict, errors: list[str]) -> dict:
        system = ("You fix extracted report fields. Re-read the report and correct only the listed errors. "
                  "Never invent a value the report does not contain: leave it null. Reply with the full JSON object only.")
        user = f"REPORT:\n{text}\n\nCURRENT FIELDS:\n{json.dumps(fields)}\n\nERRORS:\n" + "\n".join(errors)
        return self._json(system, user)

    def fix_request(self, fields: dict, errors: list[str]) -> str:
        system = "Write a short, polite email (under 80 words) asking the report's sender to fix the listed problems and resubmit."
        return self._chat(system, f"Fields: {json.dumps(fields)}\nProblems:\n" + "\n".join(errors)).strip()

    def summarize(self, fields: dict) -> str:
        system = "Write a two-sentence summary of this approved quarterly report for leadership. Plain words, include the numbers."
        return self._chat(system, json.dumps(fields)).strip()


class FakeLLM:
    """Deterministic stand-in: reads 'Field: value' lines. Lets the graph run with no model."""

    KEYS = {"organization": "organization", "country": "country", "quarter": "quarter", "year": "year",
            "contact email": "contact_email", "participants": "participants", "budget spent": "budget_spent",
            "highlights": "highlights"}

    def extract(self, text: str) -> dict:
        out = {f: None for f in FIELDS}
        for line in text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                field = self.KEYS.get(key.strip().lower())
                if field:
                    value = value.strip()
                    if field in ("year", "participants"):
                        value = int(re.sub(r"[^\d]", "", value) or 0)
                    elif field == "budget_spent":
                        value = float(re.sub(r"[^\d.]", "", value) or 0)
                    out[field] = value
        return out

    def repair(self, text: str, fields: dict, errors: list[str]) -> dict:
        fixed = dict(fields)
        country = (fixed.get("country") or "").strip().lower()
        aliases = {"usa": "US", "united states": "US", "us": "US", "canada": "Canada", "nz": "New Zealand", "new zealand": "New Zealand"}
        if country in aliases:
            fixed["country"] = aliases[country]
        q = str(fixed.get("quarter") or "").upper().replace(" ", "")
        if re.fullmatch(r"Q?[1-4]", q):
            fixed["quarter"] = "Q" + q[-1]
        return fixed

    def fix_request(self, fields: dict, errors: list[str]) -> str:
        org = fields.get("organization") or "team"
        return f"Hello {org}, your report could not be accepted yet. Please fix: " + "; ".join(errors) + ". Thank you."

    def summarize(self, fields: dict) -> str:
        return (f"{fields['organization']} ({fields['country']}) reported {fields['participants']} participants "
                f"and ${fields['budget_spent']:,.0f} spent in {fields['quarter']} {fields['year']}. {fields.get('highlights') or ''}").strip()


def get_llm():
    return FakeLLM() if config.LLM_PROVIDER == "fake" else OpenAICompatibleLLM()