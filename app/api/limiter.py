from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_key(request: Request) -> str:
    """Resolve the real client IP for rate limiting.

    Behind Cloudflare Tunnel the socket peer is always loopback, which would
    collapse all clients into a single bucket. Prefer Cloudflare's connecting
    IP, then the first hop of X-Forwarded-For, then the socket peer.
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_key)
