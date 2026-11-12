"""First LLM call: load the model named in AGENT_MODEL and ask one question."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

model_name = os.environ.get("AGENT_MODEL", "").strip()
if not model_name:
    sys.exit("AGENT_MODEL is empty in .env")
if not os.environ.get("GOOGLE_API_KEY"):
    sys.exit("GOOGLE_API_KEY is empty in .env")

llm = init_chat_model(model_name, model_provider="google_genai")
reply = llm.invoke("In one sentence, what is a distributed trace?")

usage = reply.usage_metadata or {}
reasoning = (usage.get("output_token_details") or {}).get("reasoning", 0)

print(f"Model: {model_name}")
print(f"Answer: {reply.text}")
print(f"Tokens: input={usage.get('input_tokens')} output={usage.get('output_tokens')} (of which reasoning={reasoning})")
