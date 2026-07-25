"""Bounded, safe retrieval of public web pages and PDF reports."""

from io import BytesIO
from ipaddress import ip_address
import socket
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from langchain.tools import tool
from pypdf import PdfReader
import requests

from app.core.config import WEB_SEARCH_ENABLED, WEB_SEARCH_TIMEOUT_SECONDS

MAX_FETCH_BYTES = 2_000_000
MAX_TEXT_CHARS = 12_000
MAX_PDF_PAGES = 12


def _public_http_url(url: str) -> bool:
    """Reject local/private targets so a model cannot use this as an SSRF proxy."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    try:
        return bool(addresses) and all(ip_address(item[4][0]).is_global for item in addresses)
    except ValueError:
        return False


def _read_bounded(response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=32_768):
        size += len(chunk)
        if size > MAX_FETCH_BYTES:
            raise ValueError("Document exceeds the 2 MB retrieval limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@tool
def web_fetch(url: str):
    """Fetch a public HTML page or PDF report returned by web_search.

    This tool is bounded and does not follow redirects. Use it to inspect a
    company filing, earnings release, or reputable news result before making a
    financial claim. Cite the returned source URL in the final answer.
    """

    if not WEB_SEARCH_ENABLED:
        return {"error": "Web retrieval is disabled by the administrator."}
    if not _public_http_url(url):
        return {"error": "Only resolvable public HTTP(S) URLs are allowed."}
    try:
        response = requests.get(
            url,
            timeout=WEB_SEARCH_TIMEOUT_SECONDS,
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": "ai-stack-research/1.0"},
        )
        response.raise_for_status()
        if response.is_redirect:
            return {"error": "Redirects are not followed; search for the final public URL."}
        content_type = response.headers.get("content-type", "").lower()
        payload = _read_bounded(response)
        if "pdf" in content_type or url.lower().split("?", 1)[0].endswith(".pdf"):
            reader = PdfReader(BytesIO(payload))
            text = "\n".join(
                (page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]
            )
            document_type = "pdf"
        elif "html" in content_type or "text/" in content_type or not content_type:
            text = BeautifulSoup(payload, "html.parser").get_text(" ", strip=True)
            document_type = "html"
        else:
            return {"error": f"Unsupported content type: {content_type or 'unknown'}"}
        return {
            "url": url,
            "document_type": document_type,
            "text": text[:MAX_TEXT_CHARS],
            "truncated": len(text) > MAX_TEXT_CHARS,
        }
    except (requests.RequestException, ValueError, OSError) as exc:
        return {"error": f"Web fetch failed: {exc.__class__.__name__}"}
    except Exception as exc:
        return {"error": f"Document parsing failed: {exc.__class__.__name__}"}
