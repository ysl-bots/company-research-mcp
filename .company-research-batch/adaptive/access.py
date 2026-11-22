import concurrent.futures
import ipaddress
import json
import re
import socket
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dart_job_seeker.research_batch import atomic_json

WORK = Path(__file__).resolve().parent


def allow(url):
    value = urlsplit(url)
    if value.scheme != "https" or not value.hostname or value.username or value.password:
        raise ValueError("not public HTTPS")
    if not all(ipaddress.ip_address(addr).is_global for addr in
               {entry[4][0] for entry in socket.getaddrinfo(value.hostname, 443)}):
        raise ValueError("non-public address")


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allow(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify(url):
    try:
        allow(url)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
        with build_opener(PublicRedirect()).open(req, timeout=10) as response:
            body = response.read(400000)
            title_match = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            title = title_match.group(1).decode(response.headers.get_content_charset() or "utf-8", "replace") if title_match else ""
            if len(body) < 300 or re.search(r"Access Denied|Attention Required|404 Not Found|Page Not Found", title, re.I):
                return url, {"ok": False, "detail": title or f"small response {len(body)}"}
            return url, {"ok": True, "status": response.status, "final_url": response.url,
                         "pdf": body.startswith(b"%PDF-"), "title": title[:240]}
    except (OSError, ValueError, LookupError) as exc:
        return url, {"ok": False, "detail": type(exc).__name__, "status": getattr(exc, "code", None)}


result = json.loads((WORK / "review.json").read_text())
cache_path = WORK / "access.json"
cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
urls = {item["url"] for row in result["companies"] for item in row["items"]}
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
    cache.update(pool.map(verify, sorted(urls - cache.keys())))
atomic_json(cache_path, cache)
print("checked", len(urls), "accessible", sum(cache[url]["ok"] for url in urls), flush=True)
