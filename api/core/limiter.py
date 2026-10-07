from fastapi import Request
from slowapi import Limiter

# Rate limiting by JWT username — prevents shared-IP newsroom lockouts (Example Broadcaster suggestion #5)


def get_rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            from core.auth import decode_access_token
            payload = decode_access_token(token)
            username = (payload or {}).get("sub", "")
            if username:
                return f"user:{username}"
        except Exception:
            pass
    return f"ip:{(request.client.host if request.client else 'unknown')}"


limiter = Limiter(key_func=get_rate_limit_key)
