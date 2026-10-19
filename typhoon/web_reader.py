"""Bounded, public-only article-body reader for search results.

The returned content is extracted text, never a claim to have captured an entire
webpage. Networking uses validated, pinned IP sockets (including redirects),
normal TLS certificate/hostname verification, no proxies, cookies or netrc.
"""

from __future__ import annotations

import codecs
import hashlib
import http.client
import ipaddress
import json
import io
import re
import socket
import ssl
import threading
import time
import zipfile
import zlib
from xml.etree import ElementTree
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit, parse_qs
from urllib.robotparser import RobotFileParser


USER_AGENT = "OceanQAReader/1.0"
_DNS_SLOTS = threading.BoundedSemaphore(3)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside",
         "form", "button", "iframe", "svg", "canvas", "template", "select"}
_NOISE = re.compile(r"(?:^|[\s_-])(?:ads?|advert\w*|banner|nav|menu|breadcrumb|"
                    r"footer|header|share|related|recommend\w*|comments?|sidebar|"
                    r"social|cookie|toolbar)(?:$|[\s_-])", re.I)
_CONTENT = re.compile(r"article|content|detail|trs_editor|news[_-]?(?:text|body)|"
                      r"正文|zoom|text[_-]?body", re.I)
_BLOCKS = {"p", "div", "section", "article", "main", "blockquote", "li", "ul",
           "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "pre"}
_BINARY_EXTENSIONS = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|gz|7z|png|"
                                r"jpe?g|gif|mp[34]|avi|exe)(?:$|[;/])", re.I)


class _ReadFailure(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _ReadFailure("failed", "网页读取超时")
    return remaining


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if not address.is_global or any((address.is_private, address.is_loopback,
                                        address.is_link_local, address.is_reserved,
                                        address.is_multicast, address.is_unspecified)):
            return False
        if isinstance(address, ipaddress.IPv6Address):
            # Transition addresses complicate public-destination validation.
            if address.ipv4_mapped or address.sixtofour or address.teredo or address.is_site_local:
                return False
        return True
    except ValueError:
        return False


def _safe_url(value: str) -> str:
    if (not isinstance(value, str) or len(value) > 8192 or
            re.search(r"[\x00-\x20\x7f\\]", value)):
        raise _ReadFailure("blocked", "网页地址不符合安全访问规则")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.port not in {None, 443 if parsed.scheme == "https" else 80}):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or "%" in host or len(host) > 253:
            raise ValueError()
        if ":" in host or re.fullmatch(r"[\d.]+", host):
            if not _public_ip(host):
                raise _ReadFailure("blocked", "不允许读取内网或非公开网络地址")
        elif not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError()
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit((parsed.scheme, authority,
                           quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                           quote(parsed.query, safe="%/:?@!$&'()*+,;=-._~"), ""))
    except (ValueError, UnicodeError):
        raise _ReadFailure("blocked", "网页地址不符合安全访问规则") from None


def _resolve(host: str, port: int, deadline: float) -> str:
    """Bound OS DNS latency; the bounded daemon workers cannot hold up a turn."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        budget = min(3.0, _remaining(deadline))
        started = time.monotonic()
        if not _DNS_SLOTS.acquire(timeout=budget):
            raise _ReadFailure("failed", "域名解析繁忙，请稍后重试")
        result: list = []
        done = threading.Event()

        def lookup() -> None:
            try:
                result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except OSError:
                result.append(None)
            finally:
                _DNS_SLOTS.release()
                done.set()

        threading.Thread(target=lookup, daemon=True, name="web-reader-dns").start()
        if not done.wait(max(0, budget - (time.monotonic() - started))):
            raise _ReadFailure("failed", "域名解析超时")
        if not result or not result[0]:
            raise _ReadFailure("failed", "无法解析网页域名")
        addresses = list(dict.fromkeys(entry[4][0] for entry in result[0]))
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise _ReadFailure("blocked", "域名指向内网或非公开网络地址，已停止读取")
    # The actual connection below uses this literal IP, never another DNS lookup.
    return next((address for address in addresses if ":" not in address), addresses[0])


class _DeadlineGuard:
    """Close the active socket at the absolute deadline, including slow headers."""
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.lock = threading.Lock()
        self.socket: socket.socket | None = None
        self.expired = False
        self.timer = threading.Timer(max(0, deadline - time.monotonic()), self.expire)
        self.timer.daemon = True
        self.timer.start()

    @staticmethod
    def _close(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def bind(self, sock: socket.socket) -> None:
        with self.lock:
            if self.expired or time.monotonic() >= self.deadline:
                self._close(sock)
                raise _ReadFailure("failed", "网页读取超时")
            self.socket = sock

    def expire(self) -> None:
        with self.lock:
            self.expired = True
            if self.socket is not None:
                self._close(self.socket)

    def finish(self) -> None:
        self.timer.cancel()
        with self.lock:
            if self.socket is not None:
                self._close(self.socket)
            self.socket = None


class _HeaderLimitedReader:
    """Cap aggregate response headers and chunk metadata, not just each line."""
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.count = 0

    def readline(self, limit: int = -1):
        remaining = 65536 - self.count
        line = self.wrapped.readline(min(limit, remaining + 1) if limit > 0 else remaining + 1)
        self.count += len(line)
        if self.count > 65536:
            raise _ReadFailure("failed", "网页响应头或传输元数据超过安全限制")
        return line

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


class _LimitedHTTPResponse(http.client.HTTPResponse):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fp = _HeaderLimitedReader(self.fp)


class _PinnedConnection(http.client.HTTPConnection):
    response_class = _LimitedHTTPResponse

    def __init__(self, host: str, port: int, ip: str, secure: bool,
                 deadline: float, guard: _DeadlineGuard):
        super().__init__(host, port, timeout=min(4.0, _remaining(deadline)))
        self.ip, self.secure, self.deadline, self.guard = ip, secure, deadline, guard

    def connect(self) -> None:
        # Do not use create_connection(host): validation and connection must use
        # the same address even if an attacker changes DNS between the two.
        family = socket.AF_INET6 if ":" in self.ip else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock = sock
        self.guard.bind(sock)
        sock.settimeout(min(4.0, _remaining(self.deadline)))
        try:
            sock.connect((self.ip, self.port))
            if self.secure:
                context = ssl.create_default_context()
                tls = context.wrap_socket(sock, server_hostname=self.host,
                                          do_handshake_on_connect=False)
                self.sock = tls
                self.guard.bind(tls)
                tls.settimeout(min(4.0, _remaining(self.deadline)))
                tls.do_handshake()
            self.sock.settimeout(min(4.0, _remaining(self.deadline)))
        except BaseException:
            self.close()
            raise


@dataclass
class _Download:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes = b""
    truncated: bool = False
    warning: str | None = None


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parts: list = field(default_factory=list)
    excluded: bool = False


def _node_text(node: _Node) -> str:
    if node.excluded:
        return ""
    text = "".join(_node_text(part) if isinstance(part, _Node) else part for part in node.parts)
    if node.tag in {"td", "th"}:
        return text.strip() + " | "
    if node.tag in _BLOCKS or node.tag == "br":
        return "\n" + text + "\n"
    return text


def _paragraphs(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        line = _clean(line).rstrip(" |").strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n\n".join(lines)


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.nodes: list[_Node] = []
        self.meta: dict[str, str] = {}
        self.paginated = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if len(self.nodes) >= 80000 or len(self.stack) >= 180:
            raise _ReadFailure("failed", "网页结构过于复杂，无法安全提取正文")
        # Common optional closing tags in real-world, imperfect HTML.
        if tag in {"p", "li", "tr", "td", "th"} and self.stack[-1].tag == tag:
            self.stack.pop()
        marker = attributes.get("id", "") + " " + attributes.get("class", "")
        noise=bool(_NOISE.search(marker)) and not re.search(
            r'(?:wrap(?:per)?|layout|container|with)[_-](?:header|footer|nav|sidebar)',marker,re.I)
        excluded = (self.stack[-1].excluded or tag in _SKIP or noise
                    or "hidden" in attributes or attributes.get("aria-hidden") == "true"
                    or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden",
                                      attributes.get("style", ""), re.I)))
        node = _Node(tag, attributes, excluded=excluded)
        self.stack[-1].parts.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or
                   attributes.get("itemprop") or "").lower()
            if key and attributes.get("content"):
                self.meta.setdefault(key, _clean(attributes["content"])[:500])
        if tag == "a" and "next" in attributes.get("rel", "").lower().split():
            self.paginated = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].parts.append(data)

    def extract(self) -> dict:
        title = self.meta.get("og:title", "")
        published = next((self.meta[key] for key in (
            "article:published_time", "datepublished", "pubdate", "publishdate",
            "publish_time", "firstpublishedtime", "dc.date", "dc.date.issued",
            "og:release_date", "date", "citation_online_date", "citation_date") if self.meta.get(key)), "")
        candidates: list[tuple[float, str, str]] = []
        for node in self.nodes:
            if node.tag == "title" and not title:
                title = _clean(_node_text(node))
            if node.tag == "time" and not published and node.attrs.get("datetime"):
                published = _clean(node.attrs["datetime"])
            if node.tag == "script" and node.attrs.get("type") == "application/ld+json":
                try:
                    raw = "".join(part for part in node.parts if isinstance(part, str))
                    if len(raw) < 100000:
                        data = json.loads(raw)
                        queue = list(data[:100]) if isinstance(data, list) else [data]
                        inspected = 0
                        while queue and inspected < 100:
                            obj = queue.pop(0)
                            inspected += 1
                            if isinstance(obj, dict):
                                published = published or str(obj.get("datePublished") or "")
                                if isinstance(obj.get("@graph"), list):
                                    queue.extend(obj["@graph"][:100])
                except (ValueError, TypeError):
                    pass
            if node.tag == "a" and re.fullmatch(r"下一页|下页|next", _clean(_node_text(node)), re.I):
                self.paginated = True
            if node.excluded:
                continue
            marker = node.attrs.get("id", "") + " " + node.attrs.get("class", "")
            if node.tag in {"main", "article"} or (node.tag in {"div", "section", "td"} and _CONTENT.search(marker)):
                text = _paragraphs(_node_text(node))
                factor = 1.35 if node.tag == "article" else 1.15 if node.tag == "main" else 1.0
                candidates.append((len(text) * factor, text, "html_" + node.tag))
        candidates = [candidate for candidate in candidates if len(candidate[1]) >= 20]
        if candidates:
            _, content, method = max(candidates, key=lambda item: item[0])
            fallback = False
        else:
            body = next((node for node in self.nodes if node.tag == "body"), self.root)
            content = _paragraphs(_node_text(body))
            method, fallback = "html_visible_text_fallback", True
        return {"content": content, "page_title": title[:300] or None,
                "published_at": _clean(published)[:100] or None,
                "extraction_method": method, "fallback": fallback,
                "paginated": self.paginated,
                "document_links": [{'href': node.attrs.get('href', ''), 'title': _clean(_node_text(node))[:200]}
                                   for node in self.nodes if node.tag == 'a' and not node.excluded
                                   and re.search(r'\.(?:pdf|docx)(?:$|[?#])', node.attrs.get('href', ''), re.I)][:8]}


def _decode(body: bytes, content_type: str) -> str:
    if body.startswith(codecs.BOM_UTF8):
        return body.decode("utf-8-sig", errors="replace")
    if body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return body.decode("utf-16", errors="replace")
    meta = re.search(br"<meta\b[^>]*charset\s*=\s*[\"']?\s*([\w-]+)", body[:8192], re.I)
    header = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    encodings = ([meta.group(1).decode("ascii")] if meta else [])
    encodings += [header.group(1)] if header else []
    encodings += ["utf-8", "gb18030"]
    for encoding in dict.fromkeys(encodings):
        try:
            if encoding.lower().replace("-", "") in {"gbk", "gb2312"}:
                encoding = "gb18030"
            return body.decode(encoding)
        except (UnicodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


class WebPageReader:
    def __init__(self, *, enabled: bool = True, timeout_seconds: float = 12.0,
                 max_pages: int = 3, max_chars: int = 48000, max_bytes: int = 2000000,
                 allow_official_documents: bool = False):
        self.enabled = bool(enabled)
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 60.0))
        self.max_pages = max(1, min(int(max_pages), 10))
        self.max_chars = max(1, min(int(max_chars), 250000))
        self.max_bytes = max(128, min(int(max_bytes), 4000000))
        self.allow_official_documents = bool(allow_official_documents)
        self._robots_cache: dict[str, tuple[float, RobotFileParser | bool]] = {}
        self._robots_lock = threading.Lock()

    def status(self) -> dict:
        return {"enabled": self.enabled, "timeout_seconds": self.timeout_seconds,
                "max_pages": self.max_pages, "max_chars": self.max_chars,
                "max_bytes": self.max_bytes, "parallelism": 3,
                "content_scope": "extracted_article_body", "respects_robots_txt": True}

    def _document_url_allowed(self, url):
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        trusted=(host == 'gov.cn' or host.endswith('.gov.cn') or host.endswith('.noaa.gov')
                or host in {'arxiv.org','aclanthology.org','nhess.copernicus.org','gmd.copernicus.org','nora.nerc.ac.uk'})
        attachment=bool(re.search(r'\.(?:pdf|docx)$',parsed.path,re.I))
        # JCMS government attachments use an opaque filename on one fixed
        # endpoint. Do not treat arbitrary query URLs/paths as document links.
        if host.endswith('.gov.cn') and parsed.path=='/module/download/downfile.jsp':
            names=parse_qs(parsed.query).get('filename',[])
            attachment=len(names)==1 and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,160}\.(?:pdf|docx)',names[0],re.I))
        return bool(self.allow_official_documents and trusted and attachment)

    def _document_text(self, body, is_pdf):
        if is_pdf:
            from pypdf import PdfReader
            document = PdfReader(io.BytesIO(body))
            if document.is_encrypted or len(document.pages) > 120:
                raise _ReadFailure('unsupported', '附件已加密或超过页数限制')
            parts, length = [], 0
            for index, page in enumerate(document.pages, 1):
                text = page.extract_text() or ''
                if text.strip():
                    parts.append(f'第{index}页\n{text}')
                length += len(text)
                if length > self.max_chars:
                    break
            # Generated page labels are navigation metadata, not source text.
            # A long scanned PDF can otherwise pass the minimum-body check.
            if len(re.sub(r'\s+', '', '\n'.join(
                    part.split('\n', 1)[1] for part in parts))) < 30:
                raise _ReadFailure('failed', '附件没有足够可提取正文，扫描或图片内容需要OCR核验')
            return '\n\n'.join(parts)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = archive.infolist()
            if len(members) > 500 or sum(item.file_size for item in members) > 12000000:
                raise _ReadFailure('unsupported', '附件解压大小超过限制')
            document_xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in document_xml or b'<!ENTITY' in document_xml:
                raise _ReadFailure('unsupported', '附件XML包含不支持的声明')
            root = ElementTree.fromstring(document_xml)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            return '\n\n'.join(''.join(node.itertext()) for node in root.findall('.//w:p', ns))

    @staticmethod
    def _official(item: dict) -> bool:
        try:
            host = (urlsplit(str(item.get("url") or "")).hostname or "").lower()
            return any(host == domain or host.endswith("." + domain) for domain in (
                "gov.cn", "gov", "nmc.cn", "weather.com.cn", "jma.go.jp", "wmo.int",
                "zjocean.org.cn", "hxzs.com.cn"))
        except ValueError:
            return False

    def enrich(self, results: list[dict]) -> list[dict]:
        """Return copies in original order, retaining snippets even on failure."""
        output = [dict(item, read_status="not_attempted" if self.enabled else "disabled",
                       read_error=None, content="", content_chars=0, content_sha256=None,
                       final_url=None, page_title=None, read_at=None, truncated=False,
                       extraction_method=None, coverage_caveat="尚未提取网页正文" if self.enabled else "网页正文读取已关闭")
                  for item in results]
        for item in output:
            item.setdefault("published_at", None)
        if not self.enabled or not output:
            return output
        selected = sorted(range(len(output)), key=lambda index: (not self._official(output[index]), index))[:self.max_pages]
        with ThreadPoolExecutor(max_workers=min(3, len(selected)), thread_name_prefix="web-reader") as executor:
            futures = {index: executor.submit(self._read_page, output[index].get("url", "")) for index in selected}
            for index, future in futures.items():
                result = future.result()
                if not result.get("published_at"):
                    result["published_at"] = output[index].get("published_at")
                output[index].update(result)
        return output

    def _request(self, url: str, deadline: float, guard: _DeadlineGuard, limit: int) -> _Download:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = 443 if parsed.scheme == "https" else 80
        address = _resolve(host, port, deadline)
        connection = _PinnedConnection(host, port, address, parsed.scheme == "https", deadline, guard)
        data = bytearray()
        response = None
        try:
            target = parsed.path + ("?" + parsed.query if parsed.query else "")
            connection.request("GET", target, headers={
                "Host": parsed.netloc, "User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5", "Connection": "close"})
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            downloaded = _Download(url, response.status, headers)
            if response.status != 200 and response.status != 206:
                return downloaded
            encoding = headers.get("content-encoding", "identity").lower().strip()
            if encoding not in {"", "identity", "gzip", "deflate"}:
                raise _ReadFailure("unsupported", "服务器返回了不支持的正文压缩格式")
            try:
                while len(data) <= limit:
                    _remaining(deadline)
                    if connection.sock is not None:
                        connection.sock.settimeout(min(4.0, _remaining(deadline)))
                    chunk = response.read1(min(32768, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            except (OSError, http.client.HTTPException, _ReadFailure):
                if not data:
                    raise _ReadFailure("failed", "网页正文读取超时或连接中断") from None
                downloaded.truncated = True
                downloaded.warning = "连接中断或超时，仅提取到部分正文"
            try:
                declared_length = int(headers.get("content-length", "0"))
            except ValueError:
                declared_length = 0
            if len(data) > limit or declared_length > limit or response.status == 206:
                downloaded.truncated = True
                downloaded.warning = downloaded.warning or "正文下载达到长度限制或服务器仅返回片段"
            elif declared_length > len(data):
                downloaded.truncated = True
                downloaded.warning = "连接提前结束，仅提取到部分正文"
            downloaded.body = bytes(data[:limit])
            if encoding in {'gzip', 'deflate'}:
                try:
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == 'gzip' else zlib.MAX_WBITS)
                    expanded = decoder.decompress(downloaded.body, limit + 1)
                except zlib.error:
                    raise _ReadFailure('unsupported', '压缩正文损坏或无法安全解压') from None
                if len(expanded) > limit or decoder.unconsumed_tail or decoder.unused_data or not decoder.eof:
                    downloaded.truncated = True
                    downloaded.warning = '压缩正文达到解压限制或未完整读取，仅保留部分正文'
                downloaded.body = expanded[:limit]
            return downloaded
        except _ReadFailure:
            raise
        except (OSError, http.client.HTTPException):
            raise _ReadFailure("failed", "网页连接失败、证书验证失败或读取超时") from None
        finally:
            if response is not None:
                response.close()
            connection.close()

    def _fetch(self, url: str, deadline: float, guard: _DeadlineGuard, *,
               limit: int, robots: bool = True) -> _Download:
        seen: set[str] = set()
        for _ in range(6):
            _remaining(deadline)
            url = _safe_url(url)
            if url in seen:
                raise _ReadFailure("failed", "网页发生循环跳转")
            seen.add(url)
            if robots and _BINARY_EXTENSIONS.search(urlsplit(url).path) and not self._document_url_allowed(url):
                raise _ReadFailure("unsupported", "该链接不是可提取正文的 HTML 或文本网页")
            if robots:
                self._check_robots(url, deadline, guard)
            downloaded = self._request(url, deadline, guard, limit)
            if downloaded.status in {301, 302, 303, 307, 308}:
                destination = downloaded.headers.get("location", "")
                if not destination:
                    raise _ReadFailure("failed", "网页跳转缺少目标地址")
                url = urljoin(url, destination)
                continue
            return downloaded
        raise _ReadFailure("failed", "网页跳转次数超过安全限制")

    def _check_robots(self, url: str, deadline: float, guard: _DeadlineGuard) -> None:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            cached = self._robots_cache.get(origin)
        if cached and cached[0] > time.monotonic():
            policy = cached[1]
        else:
            result = self._fetch(origin + "/robots.txt", deadline, guard, limit=64000, robots=False)
            text = _decode(result.body, result.headers.get("content-type", "")) if result.status == 200 else ""
            final = urlsplit(result.url)
            title_match = re.search(r"<title\b[^>]*>(.*?)</title>", text[:16000], re.I | re.S)
            title = _clean(title_match.group(1)) if title_match else ""
            challenge = bool(re.search(
                r"captcha|access denied|安全验证|人机验证|验证码|访问受限|sign in|log in|just a moment",
                title, re.I) or re.search(
                r"verify you are human|checking your browser|enable javascript and cookies|"
                r"请完成.{0,12}验证|登录后.{0,12}(?:阅读|查看)", text[:16000], re.I))
            # Some official sites redirect every missing path (robots included)
            # to their normal homepage. That is a missing robots file, not an
            # access restriction. Only recognize this narrow same-origin case;
            # arbitrary HTML, challenges, denials and actual rules stay closed.
            soft_missing = (result.status == 200 and not challenge and
                            f"{final.scheme}://{final.netloc}" == origin and
                            final.path in {"", "/"} and not final.query and
                            bool(re.search(r"<\s*(?:html\b|!doctype\s+html\b)", text[:2000], re.I)) and
                            not re.search(r"(?im)^\s*(?:user-agent|disallow|allow)\s*:", text))
            if result.status in {404, 410}:
                policy = True
            elif soft_missing:
                policy = True
            elif result.status == 200 and not result.truncated:
                mime = result.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime not in {"", "text/plain", "text/html"}:
                    raise _ReadFailure("blocked", "站点访问规则不是可读取的文本，已停止自动读取")
                if "\x00" in text or re.search(r"<\s*(?:html|!doctype)|captcha|access denied|安全验证", text[:2000], re.I):
                    raise _ReadFailure("blocked", "站点访问规则返回验证页面，已停止自动读取")
                policy = RobotFileParser()
                policy.parse(text.splitlines())
            else:
                raise _ReadFailure("blocked", "无法确认站点访问规则或站点限制自动读取")
            with self._robots_lock:
                if len(self._robots_cache) >= 128:
                    self._robots_cache.pop(next(iter(self._robots_cache)))
                self._robots_cache[origin] = (time.monotonic() + 300.0, policy)
        if policy is not True and not policy.can_fetch(USER_AGENT, url):
            raise _ReadFailure("blocked", "站点 robots.txt 不允许自动读取此网页")

    def _read_page(self, url: str) -> dict:
        result = {"read_at": datetime.now(timezone.utc).isoformat(), "read_status": "failed",
                  "read_error": None, "content": "", "content_chars": 0,
                  "content_sha256": None, "final_url": None, "page_title": None,
                  "published_at": None, "truncated": False, "extraction_method": None,
                  "coverage_caveat": "未取得网页正文；搜索摘要不代表网页内容"}
        # Official PDF/DOCX files can contain images and download more slowly
        # than an HTML article. Keep their separate budget bounded at 20s.
        budget = max(self.timeout_seconds, 20) if self._document_url_allowed(url) else self.timeout_seconds
        deadline = time.monotonic() + budget
        guard = _DeadlineGuard(deadline)
        try:
            safe = _safe_url(url)
            result["final_url"] = safe
            downloaded = self._fetch(safe, deadline, guard, limit=self.max_bytes)
            result["final_url"] = downloaded.url
            original, final = urlsplit(safe), urlsplit(downloaded.url)
            if (original.path not in {"", "/"} and final.path in {"", "/"} and
                    original.netloc == final.netloc and not final.query):
                raise _ReadFailure("failed", "文章链接跳转到了网站首页，未取得该文章正文")
            if downloaded.status in {401, 403, 407, 429, 451}:
                raise _ReadFailure("blocked", "网页要求登录、限制访问或限制自动读取")
            if downloaded.status not in {200, 206}:
                raise _ReadFailure("failed", "网页返回错误或无可读取的正文")
            content_type = downloaded.headers.get("content-type", "").lower()
            mime = content_type.split(";", 1)[0].strip()
            is_document = downloaded.body.lstrip().startswith((b'%PDF', b'PK\x03\x04'))
            if is_document and self._document_url_allowed(downloaded.url):
                if downloaded.truncated:
                    raise _ReadFailure('unsupported', '附件下载不完整，不能作为正文依据')
                is_pdf = downloaded.body.lstrip().startswith(b'%PDF')
                content = self._document_text(downloaded.body, is_pdf)
                if len(re.sub(r'\s+', '', content)) < 30:
                    raise _ReadFailure('failed', '附件没有足够可提取文字，图片内容尚未识别')
                result.update(content=content[:self.max_chars], content_chars=len(content[:self.max_chars]),
                              content_sha256=hashlib.sha256(content[:self.max_chars].encode()).hexdigest(),
                              read_status='partial' if len(content) > self.max_chars else 'success',
                              truncated=len(content) > self.max_chars,
                              extraction_method='official_pdf_text' if is_pdf else 'official_docx_text',
                              coverage_caveat='仅提取附件文字；未识别图片、图表或外部链接。')
                return result
            if (mime not in {"", "text/html", "application/xhtml+xml", "text/plain"} or
                    downloaded.body.lstrip().startswith((b"%PDF", b"PK\x03\x04"))):
                raise _ReadFailure("unsupported", "此资源格式不支持网页正文提取")
            text = _decode(downloaded.body, content_type)
            if "\x00" in text:
                raise _ReadFailure("unsupported", "此资源不是可读取的网页文本")
            parser = _ArticleParser()
            if mime == "text/plain":
                extracted = {"content": _paragraphs(text), "page_title": None,
                             "published_at": None, "extraction_method": "plain_text",
                             "fallback": False, "paginated": False}
            else:
                parser.feed(text)
                parser.close()
                extracted = parser.extract()
            title = extracted["page_title"] or ""
            if (re.search(r"captcha|/login(?:[/.?]|$)|/signin|/challenge|/sorry/", downloaded.url, re.I)
                    or re.search(r"安全验证|人机验证|验证码|访问受限|请先登录|登录后|"
                                 r"access denied|just a moment|sign in|log in|captcha", title, re.I)
                    or (len(extracted["content"]) < 3000 and re.search(
                        r"请完成.{0,12}验证|验证.{0,8}不是机器人|enable javascript and cookies|"
                        r"checking your browser|登录后.{0,12}(?:阅读|查看)|verify you are human|"
                        r"(?:sign in|subscribe) to (?:continue|read)|subscription required|"
                        r"(?:订阅|付费)后.{0,12}(?:阅读|查看)",
                        text, re.I))
                    or (len(extracted["content"]) < 1000 and re.search(
                        r"<input\b[^>]*type\s*=\s*[\"']?password", text, re.I))):
                raise _ReadFailure("blocked", "网页要求安全验证或登录，未尝试绕过访问限制")
            content = extracted.pop("content")
            if len(content) < 20:
                raise _ReadFailure("failed", "未提取到足够正文；页面可能需要脚本加载")
            caveats = []
            if downloaded.warning:
                caveats.append(downloaded.warning)
            if len(content) > self.max_chars:
                caveats.append("正文超过字符上限，仅保留前段")
            if extracted.pop("fallback"):
                caveats.append("未定位到明确文章区，使用清理后的可见文本，可能含少量非正文")
            if extracted.pop("paginated"):
                caveats.append("网页可能有分页，未读取其他分页")
            if "\ufffd" in content:
                caveats.append("部分文字编码无法完整还原")
            result.update(extracted)
            result['document_links'] = [{**link, 'url': urljoin(downloaded.url, link['href'])}
                                        for link in result.get('document_links', [])]
            result.update(read_status="partial" if caveats or downloaded.truncated else "success",
                          content=content[:self.max_chars], truncated=downloaded.truncated or len(content) > self.max_chars,
                          read_error="；".join(caveats) or None,
                          coverage_caveat="仅为提取到的网页正文，不含图片、附件或脚本动态内容" +
                                          ("；" + "；".join(caveats) if caveats else ""))
            result["content_chars"] = len(result["content"])
            result["content_sha256"] = hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
        except _ReadFailure as exc:
            result.update(read_status=exc.status, read_error=exc.message)
        except Exception:
            # Network exceptions may contain credential-bearing input or URLs.
            result.update(read_status="failed", read_error="网页读取或正文解析失败")
        finally:
            guard.finish()
        return result
