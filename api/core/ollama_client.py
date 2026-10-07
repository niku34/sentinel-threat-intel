import os

import httpx


class OllamaClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.bearer_token = os.getenv("OLLAMA_BEARER_TOKEN", "")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def chat(self, model: str, messages: list, stream: bool = False, timeout: int = 60) -> dict:
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/chat",
                    headers=self._headers(),
                    json={"model": model, "messages": messages, "stream": stream},
                )
                res.raise_for_status()
                return res.json()
        except Exception as e:
            return {"message": {"content": ""}, "error": str(e)}

    def generate(self, model: str, prompt: str, stream: bool = False, timeout: int = 60) -> dict:
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/generate",
                    headers=self._headers(),
                    json={"model": model, "prompt": prompt, "stream": stream},
                )
                res.raise_for_status()
                return res.json()
        except Exception as e:
            return {"response": "", "error": str(e)}

    def tags(self) -> list:
        try:
            with httpx.Client(timeout=5) as client:
                res = client.get(
                    f"{self.base_url}/api/tags",
                    headers=self._headers(),
                )
                res.raise_for_status()
                return res.json().get("models", [])
        except Exception:
            return []


ollama_client = OllamaClient()
