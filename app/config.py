"""Settings, read from environment variables so the same code runs on any OpenAI-compatible provider."""
import os

# Any server that speaks the OpenAI chat API works: Ollama, NVIDIA NIM, Gemini, vLLM, OpenAI itself.
#   Ollama (local):  LLM_BASE_URL=http://localhost:11434/v1          LLM_MODEL=gpt-oss:20b
#   NVIDIA NIM:      LLM_BASE_URL=https://integrate.api.nvidia.com/v1 LLM_MODEL=openai/gpt-oss-20b
#   Gemini:          LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  LLM_MODEL=gemini-2.5-flash
# LLM_PROVIDER=fake runs a rule-based stand-in with no model at all (tests and offline demos).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compatible")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5:0.8b")

DB_PATH = os.getenv("DB_PATH", "data/reports.db")
CHECKPOINT_PATH = os.getenv("CHECKPOINT_PATH", "data/checkpoints.db")
MAX_REPAIRS = int(os.getenv("MAX_REPAIRS", "2"))

VALID_COUNTRIES = ["US", "Canada", "New Zealand"]

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))          # seconds per model call
LLM_NO_THINK = os.getenv("LLM_NO_THINK", "false").lower() == "true"  # set true for Qwen models
LLM_JSON_MODE = os.getenv("LLM_JSON_MODE", "false").lower() == "true"  # strict JSON output; some local models stall with it
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))  # caps each answer so a model cannot run forever