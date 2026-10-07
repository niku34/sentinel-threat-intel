from fastapi import APIRouter
from pydantic import BaseModel

from core.ollama_client import ollama_client

router = APIRouter(prefix="/api/llm-wiki", tags=["llm-wiki"])

_MODEL = "qwen2.5:14b"
_TIMEOUT = 60

_SYSTEM_PROMPT = (
    "You are Sentinel Assistant, an AI security expert for Example Broadcaster's broadcast "
    "security platform. You know everything about: Kaaval AI (Tamil threat intelligence, "
    "deepfake detection, brigade detection), Digital Kavasam (YouTube channel protection, "
    "CERT-In compliance, DPDP Act), Veriff (fact-checking, Tamil NLP, 7 fact-check "
    "databases), Indian cybersecurity law (CERT-In Directions 2022, DPDP Act 2023, IT Act "
    "2000), and Tamil broadcast media threats. Answer in English. Be concise and practical."
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


@router.post("/chat")
def chat(body: ChatRequest):
    messages = (
        [{"role": "system", "content": _SYSTEM_PROMPT}]
        + [{"role": m.role, "content": m.content} for m in body.history]
        + [{"role": "user", "content": body.message}]
    )
    data = ollama_client.chat(model=_MODEL, messages=messages, timeout=_TIMEOUT)
    if "error" in data or not data.get("message", {}).get("content"):
        return {"response": "Assistant unavailable — Ollama not responding", "model": "offline"}
    return {"response": data["message"]["content"], "model": _MODEL}
