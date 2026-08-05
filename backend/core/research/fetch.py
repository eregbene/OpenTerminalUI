from __future__ import annotations

import io
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from backend.core.research.clean import clean_text, html_to_text

logger = logging.getLogger(__name__)

# Many sites (e.g. Wikipedia) reject the default httpx UA with HTTP 403.
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 BensimTrading/research"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def pdf_to_text(data: bytes, *, max_chars: int = 200_000) -> str:
    """Extract a bounded text representation from PDF data."""
    try:
        reader = PdfReader(io.BytesIO(data))
        return clean_text("\n".join(page.extract_text() or "" for page in reader.pages), max_chars=max_chars)
    except Exception as exc:
        logger.warning("Failed to extract PDF text: %s", exc)
        return ""


def arxiv_pdf_url(value: str) -> str:
    """Return an arXiv PDF URL from an id or abs/PDF URL."""
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme or parsed.netloc else raw
    path = path.strip("/")
    path = re.sub(r"^(?:abs|pdf)/", "", path)
    path = re.sub(r"\.pdf$", "", path, flags=re.IGNORECASE)
    return f"https://arxiv.org/pdf/{path}.pdf" if path else ""


def _html_title(html: str, url: str) -> str:
    try:
        soup = BeautifulSoup(html, features="lxml")
        title = clean_text(soup.title.get_text(" ") if soup.title else "")
        if not title:
            heading = soup.find("h1")
            title = clean_text(heading.get_text(" ") if heading else "")
        return title or url
    except Exception:
        return url


async def fetch_url(url: str, *, timeout: float = 25.0, max_chars: int = 200_000) -> dict:
    """Fetch a URL and normalize its useful textual content."""
    try:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=True, timeout=timeout, headers=_REQUEST_HEADERS
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        is_pdf = content_type.startswith("application/pdf") or urlparse(str(response.url)).path.lower().endswith(".pdf")
        if is_pdf:
            text = pdf_to_text(response.content, max_chars=max_chars)
            title = url
        elif not content_type or content_type == "text/html" or content_type.endswith("+html"):
            raw_html = response.text
            text = html_to_text(raw_html, max_chars=max_chars)
            title = _html_title(raw_html, url)
            content_type = content_type or "text/html"
        else:
            text = clean_text(response.text, max_chars=max_chars)
            title = url

        return {
            "source": "web",
            "external_id": url,
            "title": title or url,
            "url": url,
            "abstract": text[:500],
            "text": text,
            "content_type": content_type,
            "published_at": "",
        }
    except Exception as exc:
        logger.warning("Failed to fetch URL %s: %s", url, exc)
        return {}


async def fetch_arxiv_fulltext(pdf_url: str, *, timeout: float = 30.0, max_chars: int = 200_000) -> str:
    """Fetch and extract an arXiv PDF without surfacing network or parse errors."""
    target = arxiv_pdf_url(pdf_url)
    if not target:
        return ""
    try:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=True, timeout=timeout, headers=_REQUEST_HEADERS
        ) as client:
            response = await client.get(target)
            response.raise_for_status()
        return pdf_to_text(response.content, max_chars=max_chars)
    except Exception as exc:
        logger.warning("Failed to fetch arXiv full text %s: %s", target, exc)
        return ""
