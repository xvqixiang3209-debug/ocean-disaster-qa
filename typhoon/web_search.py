"""Public web search with automatic multi-provider fallback.

This module discovers result titles, snippets and URLs. The separate web_reader
retrieves source bodies; neither layer treats them as verified observations.
"""

from __future__ import annotations

import base64
import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit

import requests


PROVIDER_LABELS = {
    "baidu": "百度",
    "bing": "Bing 中国版",
    "so": "360搜索",
    "sogou": "搜狗搜索",
}
PROVIDER_HOSTS = {
    "baidu": "https://www.baidu.com",
    "bing": "https://cn.bing.com",
    "so": "https://www.so.com",
    "sogou": "https://www.sogou.com",
}
PROVIDER_ORDERS = {
    # 360 and Sogou currently provide useful Chinese results without requiring
    # an interactive browser challenge. Baidu remains a last-resort source in
    # automatic mode because server IPs are frequently sent to its CAPTCHA.
    "auto": ("so", "sogou", "bing", "baidu"),
    "baidu": ("baidu", "so", "sogou", "bing"),
    # Explicit non-Baidu choices stay strict, which is useful when a deployment
    # permits only one search host. Use auto for resilient fallback.
    "bing": ("bing",),
    "so": ("so",),
    "sogou": ("sogou",),
}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}


def normalize_provider(value: str) -> str:
    name = str(value or "").strip().lower()
    if name == "edge":
        name = "bing"
    if name == "360":
        name = "so"
    if name not in PROVIDER_ORDERS:
        raise ValueError(
            "TYPHOON_WEB_SEARCH_PROVIDER 仅支持 auto、baidu、bing/edge、so/360、sogou"
        )
    return name


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _valid_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def _result_url(value: str, base: str) -> str:
    try:
        value = urljoin(base, html.unescape(value).strip())
    except ValueError:
        return ""
    if not _valid_url(value):
        return ""
    parsed = urlsplit(value)
    # Bing sometimes wraps the actual URL in an a1/base64 tracking link.
    if (parsed.hostname or "").endswith(".bing.com") and parsed.path.startswith("/ck/"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                raw = encoded[2:]
                decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
                return decoded if _valid_url(decoded) else ""
            except (ValueError, UnicodeError):
                return ""
    return value


class _ResultParser(HTMLParser):
    """Extract result cards, including nested headings and HTML void tags."""

    def __init__(self, provider: str):
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.items: list[dict] = []
        self.stack: list[str] = []
        self.root_depth: int | None = None
        self.heading_depth: int | None = None
        self.snippet_depth: int | None = None
        self.item: dict = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if self.provider == "bing":
            starts_result = tag == "li" and "b_algo" in classes
        elif self.provider == "baidu":
            starts_result = (tag == "div" and bool(classes & {"result", "result-op"})
                             and "c-container" in classes)
        elif self.provider == "so":
            starts_result = tag == "li" and "res-list" in classes
        else:  # sogou
            starts_result = tag == "div" and bool(classes & {"vrwrap", "reactResult"})
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        if self.root_depth is None and starts_result:
            self.root_depth = len(self.stack)
            self.item = {"title": "", "url": "", "direct_url": "", "snippet": "", "text": ""}
        if self.root_depth is None:
            return
        # 360 exposes the publisher URL in data-mdurl while the visible href
        # may point at a JavaScript tracking redirect. Sogou commonly uses
        # data-url for the same purpose. Prefer these explicit destinations so
        # the reader and the citation both point at the source article.
        direct_url = attrs.get("data-mdurl") or attrs.get("data-url") or ""
        if direct_url and not self.item["direct_url"] and _valid_url(direct_url):
            self.item["direct_url"] = direct_url
        if tag in {"h2", "h3"}:
            self.heading_depth = len(self.stack)
        if tag == "a" and self.heading_depth and not self.item["url"]:
            self.item["url"] = attrs.get("href") or ""
        snippet_classes = {
            "c-abstract", "content-right_8Zs40", "c-span-last",
            "res-desc", "res-list-summary", "fz-mid", "space-txt",
        }
        is_sogou_summary = str(attrs.get("id") or "").startswith("cacheresult_summary_")
        if (tag == "p" or classes & snippet_classes or is_sogou_summary
                or any(c.startswith("content-right_") for c in classes)):
            if self.snippet_depth is None:
                self.snippet_depth = len(self.stack)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.root_depth is None or any(tag in self.stack for tag in ("script", "style")):
            return
        self.item["text"] += data + " "
        if self.heading_depth:
            self.item["title"] += data
        elif self.snippet_depth:
            self.item["snippet"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.root_depth is not None and depth <= self.root_depth:
            title = _clean(self.item["title"])
            url = _result_url(self.item["direct_url"] or self.item["url"],
                              PROVIDER_HOSTS[self.provider])
            snippet = _clean(self.item["snippet"]) or _clean(self.item["text"]).replace(title, "", 1).strip()
            if title and url:
                self.items.append({"title": title[:250], "url": url,
                                   "snippet": snippet[:1200], "published_at": "",
                                   "source": urlsplit(url).hostname or PROVIDER_LABELS[self.provider],
                                   "search_provider": PROVIDER_LABELS[self.provider]})
            self.root_depth = self.heading_depth = self.snippet_depth = None
            self.item = {}
        if self.heading_depth is not None and depth <= self.heading_depth:
            self.heading_depth = None
        if self.snippet_depth is not None and depth <= self.snippet_depth:
            self.snippet_depth = None
        del self.stack[depth - 1:]


@dataclass
class SearchOutcome:
    results: list[dict] = field(default_factory=list)
    state: str = "not_requested"
    provider: str | None = None
    error: str | None = None
    attempts: list[dict] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return bool(self.attempts)

    def metadata(self) -> dict:
        return {
            "web_search_executed": self.executed,
            "web_search": bool(self.results),
            "web_search_result_count": len(self.results),
            "web_search_state": self.state,
            "web_search_provider": self.provider,
            "web_search_error": self.error,
            "web_search_attempts": self.attempts,
        }


class SearchUnavailable(RuntimeError):
    pass


class WebSearchClient:
    def __init__(self, *, enabled: bool = True, provider: str = "baidu",
                 timeout_seconds: float = 8.0, max_results: int = 5):
        self.enabled = enabled
        self.provider = normalize_provider(provider)
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = max(1, min(int(max_results), 10))

    def search(self, query: str, *, max_results: int | None = None) -> list[dict]:
        """Compatibility wrapper; conversations use the full per-request outcome."""
        return self.search_with_status(query, max_results=max_results).results

    def search_with_status(self, query: str, *, max_results: int | None = None,
                           subject: str = "", aliases: tuple[str, ...] = ()) -> SearchOutcome:
        if not self.enabled:
            return SearchOutcome(state="disabled")
        query = _clean(query)[:400]
        if not query:
            return SearchOutcome(state="empty")
        subject = _clean(subject).replace('"', "")[:80]
        if not subject:
            quoted = re.search(r'"([^"]+)"', query)
            subject = quoted.group(1) if quoted else ""
        identities = tuple(_clean(x) for x in (subject, *aliases) if _clean(x))
        limit = max(1, min(int(max_results or self.max_results), 10))
        # A short-name retry is useful for overly restrictive combined queries.
        attempts = [(p, query) for p in self.provider_order]
        retry_provider = next((p for p in self.provider_order if p != "baidu"),
                              self.provider_order[0])
        cyclone_query = any(term in query.casefold() for term in (
            '台风', '热带气旋', 'typhoon', 'tropical cyclone', 'tropical storm'))
        concise_query = f"{subject} 台风" if subject and cyclone_query else ""
        # Mixed Chinese/English impact queries for an old Western Pacific
        # storm can be too restrictive. Try the storm-specific concise form
        # immediately after the first provider attempt, before the shared
        # timeout budget is consumed by every provider.
        if ("tropical cyclone" in query.casefold() and concise_query
                and query != concise_query):
            attempts.insert(1, (retry_provider, concise_query))
        if subject and query != f'"{subject}"':
            attempts.append((retry_provider, f'"{subject}"'))
        # Bing sometimes over-segments a Chinese name on a name-only query or
        # degrades a long combined query.  A concise “名称 + 台风” retry is both
        # specific enough for identity filtering and broad enough to retrieve
        # authoritative background pages.
        if (concise_query and query != f'"{subject}"'
                and all(existing_query != concise_query for _, existing_query in attempts)):
            attempts.append((retry_provider, concise_query))
        outcome = SearchOutcome(state="empty")
        deadline = time.monotonic() + self.timeout_seconds
        for provider, search_query in attempts:
            remaining = deadline - time.monotonic()
            if remaining <= 0.1:
                outcome.attempts.append({"provider": PROVIDER_LABELS[provider], "state": "timeout",
                                         "reason": "联网搜索等待时间已用完"})
                break
            try:
                # Bound each attempt so Baidu cannot consume the whole fallback budget.
                timeout = min(remaining, max(0.2, self.timeout_seconds / 3))
                items = self._fetch_results(provider, search_query, timeout, deadline)
                relevant = []
                seen = set()
                for item in items:
                    haystack = f'{item["title"]} {item["snippet"]} {unquote(item["url"])}'
                    if not self._relevant(haystack, query, identities) or item["url"] in seen:
                        continue
                    seen.add(item["url"])
                    item["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                    relevant.append(item)
                query_scope = (
                    "full" if search_query == query else
                    "name_and_typhoon" if search_query == concise_query else
                    "name_only"
                )
                outcome.attempts.append({"provider": PROVIDER_LABELS[provider],
                                         "state": "success" if relevant else "empty",
                                         "result_count": len(relevant),
                                         "query_scope": query_scope})
                if relevant:
                    outcome.results = relevant[:limit]
                    outcome.provider = PROVIDER_LABELS[provider]
                    outcome.state = "success"
                    return outcome
            except requests.Timeout:
                outcome.attempts.append({"provider": PROVIDER_LABELS[provider], "state": "timeout",
                                         "reason": "连接搜索服务超时"})
            except SearchUnavailable as exc:
                outcome.attempts.append({"provider": PROVIDER_LABELS[provider], "state": "blocked",
                                         "reason": str(exc)})
            except requests.RequestException:
                # Do not expose proxy credentials or full exception URLs in the UI.
                outcome.attempts.append({"provider": PROVIDER_LABELS[provider], "state": "error",
                                         "reason": "连接搜索服务失败"})
        failures = [a for a in outcome.attempts if a["state"] in {"timeout", "blocked", "error"}]
        if failures:
            outcome.state = "partial_failure" if any(a["state"] == "empty" for a in outcome.attempts) else "failed"
            outcome.error = "；".join(dict.fromkeys(a["provider"] + "：" + a["reason"] for a in failures))
        return outcome

    @property
    def provider_order(self) -> tuple[str, ...]:
        return PROVIDER_ORDERS[self.provider]

    @staticmethod
    def _relevant(text: str, query: str, identities: tuple[str, ...]) -> bool:
        lowered = text.casefold()
        if identities:
            identity_matches = any(
                (re.search(r"(?<![a-z])" + re.escape(term.casefold()) + r"(?![a-z])", lowered)
                 if re.search(r"[a-z]", term, re.I) else term in text)
                for term in identities
            )
            if not identity_matches:
                return False
            # Short Western Pacific storm names are frequently shared by
            # people, songs and brands (for example BILLIE).  Search engines
            # also use the Chinese word ``台风`` for stage presence, so an
            # identity-name hit plus that single word is still not enough.
            cyclone_query = any(term in query.casefold() for term in (
                "台风", "热带气旋", "typhoon", "tropical cyclone", "tropical storm",
            ))
            obvious_other_subject = any(term in lowered for term in (
                "billie eilish", "碧梨", "歌手", "演唱", "演唱会", "专辑", "歌曲",
                "音乐", "舞台表现", "台风样样", "reaction", "official site", "store",
                "album", "music", "merch", "singer", "billie cafe", "咖啡",
            ))
            if cyclone_query and obvious_other_subject:
                return False
            ascii_identity = any(re.search(r"[a-z]", term, re.I) for term in identities)
            strong_cyclone_context = any(term in lowered for term in (
                "号台风", "热带气旋", "热带风暴", "台风路径", "西北太平洋",
                "中心气压", "最大风速", "风暴潮", "登陆", "typhoon",
                "tropical cyclone", "tropical storm", "storm track",
            ))
            cyclone_context = strong_cyclone_context or (
                not ascii_identity and "台风" in lowered
            )
            if cyclone_query and not cyclone_context:
                return False

            # When the selected storm year is known, reject results that only
            # identify a different year/reused storm name.  A two-digit storm
            # number such as 7613 is also enough to detect a 1976/1973 mix-up.
            requested_years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query))
            result_years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text))
            if requested_years and result_years and requested_years.isdisjoint(result_years):
                return False
            if requested_years:
                requested_suffixes = {year[2:] for year in requested_years}
                numbered_years = set(re.findall(
                    r"(?<!\d)(\d{2})(?:0[1-9]|[12]\d|3\d)号台风", text
                ))
                if numbered_years and requested_suffixes.isdisjoint(numbered_years):
                    return False
            return True
        # Search operators and domain suffixes are not topical evidence.
        # Otherwise “site:weather.com.cn” accepted any result containing “com”.
        topical_query = re.sub(r'\b(?:site|filetype|inurl|intitle):\S+', '', query.casefold())
        tokens = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", topical_query)
        expanded = []
        for token in tokens:
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
                expanded.extend(token[index:index + 2] for index in range(len(token) - 1))
            else:
                expanded.append(token)
        tokens = [
            token for token in expanded
            if token not in {"typhoon", "storm", "搜索", "新闻", "报道", "截至", "问题", "多少", "几个"}
        ]
        return any(token in lowered for token in tokens)

    def _fetch_results(self, provider: str, query: str, timeout: float, deadline: float) -> list[dict]:
        base = PROVIDER_HOSTS[provider]
        if provider == "baidu":
            path, params = "/s?", {"wd": query}
        elif provider == "bing":
            path, params = "/search?", {"q": query, "ensearch": "0"}
        elif provider == "so":
            path, params = "/s?", {"q": query}
        else:
            path, params = "/web?", {"query": query}
        url = base + path + urlencode(params, quote_via=quote)
        # A normal browser UA avoids search pages returning degraded/garbled
        # HTML to bot-labelled clients.  This is still a plain HTTP request;
        # no browser automation, cookies, login, or CAPTCHA bypass is used.
        with requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                     "Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
                                       "Accept-Language": "zh-CN,zh;q=0.9"},
                          timeout=(timeout / 2, timeout / 2), stream=True) as response:
            if response.status_code in {403, 429}:
                raise SearchUnavailable("搜索服务限制了自动访问")
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                if time.monotonic() > deadline:
                    raise requests.Timeout()
                content.extend(chunk)
                if len(content) > 3_000_000:
                    raise SearchUnavailable("搜索页面过大")
            text = content.decode("utf-8", errors="replace")
            if any(term in response.url.lower() for term in ("captcha", "wappass.baidu.com", "/sorry/")):
                raise SearchUnavailable("搜索服务要求安全验证")
            if re.search(r"<title[^>]*>[^<]*(?:百度安全验证|安全验证|验证码|Access Denied)", text, re.I):
                raise SearchUnavailable("搜索服务要求安全验证")
            if "location.replace" in text and len(text) < 1000:
                raise SearchUnavailable("搜索服务返回了跳转验证页")
            parser = _ResultParser(provider)
            parser.feed(text)
            result_markers = {
                "baidu": ('id="content_left"',),
                "bing": ("b_results",),
                "so": ("res-list",),
                "sogou": ("vrwrap", "reactResult"),
            }
            if not parser.items and not any(marker in text for marker in result_markers[provider]):
                raise SearchUnavailable("未读取到搜索结果页")
            return parser.items

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured_provider": self.provider,
            "provider": " → ".join(PROVIDER_LABELS[p] for p in self.provider_order),
            "provider_order": list(self.provider_order),
            "timeout_seconds": self.timeout_seconds,
            "max_results": self.max_results,
        }
