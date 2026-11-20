"""Run: python debug_llm.py   Shows the settings the app sees and the raw model reply for one report."""
import json
from openai import OpenAI
from app import config
from app.llm import FIELDS

print("model:", config.LLM_MODEL, "| url:", config.LLM_BASE_URL)
print("LLM_NO_THINK:", config.LLM_NO_THINK, "| JSON mode:", config.LLM_JSON_MODE,
      "| max_tokens:", config.LLM_MAX_TOKENS, "| timeout:", config.LLM_TIMEOUT)
text = open("sample_reports/good_canada_q3.txt").read()
system = ("You extract fields from a quarterly program report. Reply with JSON only, keys: " + ", ".join(FIELDS) +
          ". Use null for anything the report does not state.")
kwargs = {"extra_body": {"reasoning_effort": "none"}} if config.LLM_NO_THINK else {}
r = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY).chat.completions.create(
    model=config.LLM_MODEL, temperature=0, max_tokens=config.LLM_MAX_TOKENS, timeout=config.LLM_TIMEOUT,
    messages=[{"role": "system", "content": system}, {"role": "user", "content": text}], **kwargs)
c = r.choices[0]
reasoning = getattr(c.message, "reasoning", None) or (c.message.model_extra or {}).get("reasoning") or ""
print("finish_reason:", c.finish_reason, "| completion_tokens:", r.usage.completion_tokens, "| thinking chars:", len(reasoning))
print("content:", repr((c.message.content or "")[:600]))