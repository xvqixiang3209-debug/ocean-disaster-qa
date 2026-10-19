"""Bounded adapters for Zhejiang named bathing-water and operator sources.

An admission schedule is never an activity permission. Observation dates, rather
than publication or retrieval dates, determine whether water data match a query.
"""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import re
import time
from urllib.parse import urljoin, urlsplit

import requests

from .storage import BEIJING, local_now
from .activity_locations import ACTIVITY_SITES


OPENING_URL = 'https://www.zhujiajian.com/art/2026/10/1/art_2247_94166.html'
QUALITY_URL = 'https://www.nmemc.org.cn/hjzl/hsycszzb/202610/t20261014_1182943.shtml'
OPERATOR_INDEX_URL = 'https://www.zhujiajian.com/'
QUALITY_INDEX_URL = 'https://www.nmemc.org.cn/hjzl/hsycszzb/'
ALLOWED_HOSTS = {'www.zhujiajian.com', 'www.nmemc.org.cn'}
MAX_BYTES = 1024 * 1024
CACHE_TTL = timedelta(hours=1)
SCHEMA = 4
MAX_DISCOVERED_PER_KIND = 3
SITE_NAMES = {
    'nansha': ('南沙景点', '南沙景区', '南沙海水浴场', '南沙浴场', '南沙'),
    'dongsha': ('东沙景点', '东沙景区', '东沙湾海滨浴场', '东沙湾', '东沙'),
    'wushitang': ('乌石塘景区', '乌石塘'),
    'baishan': ('白山景区', '白山'),
}
ACTIVITY_PATTERNS = {'swimming': r'(?:下海|下水)?游泳(?![区圈镜衣帽])|泳区',
                     'tidal_flat': r'赶海', 'shore_fishing': r'岸钓|矶钓|海钓|垂钓',
                     'boat_fishing': r'船钓|出海垂钓|出海海钓'}


class _Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts, self.rows, self.meta, self.links = [], [], {}, []
        self.anchor = None
        self.skip, self.row, self.cell = 0, None, None
        self.feed(source)
        self.text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(self.parts))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript'}:
            self.skip += 1
        if self.skip:
            return
        if tag == 'meta':
            # Preserve field-name case: this operator template retains an old
            # pubDate field alongside its verified, current lowercase pubdate.
            self.meta.setdefault(attrs.get('name', ''), attrs.get('content', ''))
        if tag == 'a':
            self.anchor = {'href': attrs.get('href', ''), 'text': []}
        if tag == 'tr':
            self.row = []
        elif tag in {'td', 'th'} and self.row is not None:
            self.cell = []
        if tag in {'p', 'div', 'br', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'a' and self.anchor is not None:
            self.links.append({'href': self.anchor['href'],
                               'text': ''.join(self.anchor['text']).strip()})
            self.anchor = None
        if tag in {'td', 'th'} and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r'\s+', '', ''.join(self.cell)))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row, self.cell = None, None
        if tag in {'p', 'div', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            if self.anchor is not None:
                self.anchor['text'].append(data)
            if self.cell is not None:
                self.cell.append(data)


def _date(value):
    match = re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?', value or '')
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def site_id_for_name(name):
    """Resolve a named operator site, without inventing a surveyed position."""
    if not name:
        return None
    if name in SITE_NAMES:
        return name
    return next((key for key, aliases in SITE_NAMES.items()
                 if any(alias in str(name) for alias in aliases)), None)


def discover_articles(html_text, index_url, kind):
    """Discover article URLs from a verified same-host public index only."""
    if index_url not in {OPERATOR_INDEX_URL, QUALITY_INDEX_URL}:
        raise ValueError('source_index_not_allowed')
    links = []
    for link in _Page(html_text).links:
        url = urljoin(index_url, link['href'])
        if not _valid_url(url) or urlsplit(url).hostname != urlsplit(index_url).hostname:
            continue
        title, path = link['text'], urlsplit(url).path
        if kind == 'bathing_water_quality':
            matched = ('海水浴场水质概况' in title
                       and re.fullmatch(r'/hjzl/hsycszzb/\d{6}/t\d+_\d+\.shtml', path))
        else:
            matched = (re.search(r'营业|开放|关闭|恢复|暂停|禁泳|游泳|赶海|垂钓|海钓|通知|公告', title)
                       and re.fullmatch(r'/art/20\d{2}/\d{1,2}/\d{1,2}/art_\d+_\d+\.html', path))
        if matched and url not in links:
            links.append(url)
    # Operator dates are present in the path. For quality indexes, retain the
    # publisher's newest-first list order; URL dates are not publication dates.
    if kind != 'bathing_water_quality':
        links.sort(key=lambda u: tuple(map(int, re.search(r'/art/(\d+)/(\d+)/(\d+)/', u).groups())), reverse=True)
    return links[:MAX_DISCOVERED_PER_KIND]


def _published(page):
    visible = re.search(r'(?:发布日期|发布时间)\s*[：:]\s*(20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?)', page.text)
    return _date(visible[1]) if visible else _date(page.meta.get('pubdate'))


def _published_time(page):
    """Keep publisher-specified clock time, while the legacy date stays stable."""
    published = _published(page)
    if not published:
        return None
    visible = re.search(r'(?:发布日期|发布时间)\s*[：:]\s*([^\n|]{10,40})', page.text)
    for value in (visible[1] if visible else '', page.meta.get('pubdate', '')):
        clock = re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?\s*[T ]\s*'
                          r'(\d{1,2}):([0-5]\d)(?::([0-5]\d))?', value)
        if clock:
            try:
                result = datetime(*map(int, clock.groups(default='0')), tzinfo=BEIJING)
            except ValueError:
                continue
            if result.date().isoformat() == published:
                return result.isoformat()
    # Do not pretend that a date-only publisher gave an exact midnight time.
    return None


def _query_as_of(day, now, as_of=None):
    """No future publication may become known just because a query is future."""
    cutoff = datetime.fromisoformat(as_of.replace('Z', '+00:00')) if isinstance(as_of, str) else as_of
    if cutoff is None:
        cutoff = datetime.combine(date.fromisoformat(str(day)), datetime.min.time(), tzinfo=BEIJING)
    if not isinstance(cutoff, datetime):
        raise ValueError('invalid_source_as_of')
    cutoff = cutoff.replace(tzinfo=BEIJING) if cutoff.tzinfo is None else cutoff.astimezone(BEIJING)
    now = now.replace(tzinfo=BEIJING) if now.tzinfo is None else now.astimezone(BEIJING)
    return min(now, cutoff)


def _published_by(source, cutoff):
    value = source.get('published_at_time') or source.get('published_at')
    if not isinstance(value, str):
        return False
    try:
        published = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=BEIJING)
    return published <= cutoff


def _activity_statement_state(clause, activity):
    """Bind a directive to its activity, not an unrelated ban in a sentence.

    This intentionally recognizes a small grammar. Conditional, indirect and
    ambiguous statements remain unresolved rather than authorizing an activity.
    """
    negative = (r'(?:尚|暂)?(?:未|不)(?:予以)?(?:(?:允许|准许)?(?:恢复开放|恢复|开放)|允许|准许)'
                r'|不再开放|禁止|严禁|暂停|停止|关闭|不准|不可|不得|勿|谢绝')
    positive = r'允许|准许|恢复开放|恢复|开放'
    directive = re.compile('(?:' + negative + ')|(?:' + positive + ')')
    states = set()
    for target in re.finditer(ACTIVITY_PATTERNS[activity], clause):
        prefix, suffix = clause[:target.start()], clause[target.end():]
        commands = list(directive.finditer(prefix))
        state = None
        if commands:
            command = commands[-1]
            gap = prefix[command.end():]
            # Allow a list governed by the same verb, e.g. 禁止游泳及海钓.
            for pattern in ACTIVITY_PATTERNS.values():
                gap = re.sub(pattern, '', gap)
            gap = re.sub(r'[、和及与\s]', '', gap)
            qualifiers = r'(?:(?:游客|人员|任何人|所有人|公众|擅自|继续|下海|下水|进行|开展|参加|活动|项目)|(?:在)?(?:南沙|东沙|乌石塘|白山)(?:景点|浴场|景区)?)*'
            if re.fullmatch(qualifiers, gap):
                text = command[0]
                # "没有禁止游泳" does not constitute explicit permission.
                if re.search(r'没有|并未|并不|并非|解除|取消|是否', prefix[max(0, command.start()-4):command.start()]):
                    state = 'unresolved'
                else:
                    state = 'verified_closed' if re.fullmatch(negative, text) else 'verified_open'
        if state is None:
            after = re.match(r'\s*(?:活动|项目|区域)?\s*(?:已|现已|仍)?\s*(' + negative + '|' + positive + ')', suffix)
            if after:
                state = 'verified_closed' if re.fullmatch(negative, after[1]) else 'verified_open'
        if state == 'verified_open' and re.search(r'另行通知|待定|未定|预计|计划|拟(?:于|在|将)|是否|视.{0,8}情况|(?:如|若|待).{0,16}(?:允许|准许|恢复|开放)', clause):
            state = 'unresolved'
        if state:
            states.add(state)
    return next(iter(states)) if len(states) == 1 else 'unresolved'


def _article_text(html_text):
    # These are the operator's article-body markers, not instructions embedded
    # in remote content. Never interpret unrelated navigation as a permission.
    match = re.search(r'<!--\s*ZJEG_RSS\.content\.begin\s*-->(.*?)<!--\s*ZJEG_RSS\.content\.end\s*-->',
                      html_text, re.S | re.I)
    if not match:
        match = re.search(r'<article\b[^>]*>(.*?)</article>', html_text, re.S | re.I)
    if not match:
        raise ValueError('operator_article_body_missing')
    return _Page(match[1]).text


def parse_operator_notice(html_text, url):
    """Extract explicit, bounded activity permissions from operator prose.

    Admission, a ticket price and a swimming water-quality grade are never
    activity permissions. Open-ended notices are retained as unresolved events;
    they cannot silently become a permanent opening or closure certificate.
    """
    page = _Page(html_text)
    published = _published(page)
    published_time = _published_time(page)
    body = _article_text(html_text)
    if not published or not re.search(r'南沙|东沙|乌石塘|白山', body):
        raise ValueError('operator_notice_not_found')
    # An explicit end date is required for machine-verifiable date coverage.
    # A single named day covers that day only. A publication date alone is not
    # an effective date; "即日起" remains an unresolved notice event.
    date_range = re.compile(
        r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日\s*'
        r'(?:[-—~～至]\s*(?:(20\d{2})年)?(?:(\d{1,2})月)?(\d{1,2})日)?')

    def interval(match, text):
        if not match:
            return None, None
        y1, m1, d1, y2, m2, d2 = match.groups()
        first = date(int(y1 or published[:4]), int(m1), int(d1)).isoformat()
        last = date(int(y2 or y1 or published[:4]), int(m2 or m1), int(d2 or d1)).isoformat()
        if last < first:
            raise ValueError('notice_effective_date_reversed')
        if not d2 and re.match(r'\s*(?:起|至另行通知)', text[match.end():]):
            last = None
        return first, last

    evidence, events = [], []
    for paragraph in re.split(r'[\n。]+', body):
        paragraph_sites = [key for key, aliases in SITE_NAMES.items()
                 if any(alias in paragraph for alias in aliases)]
        if not paragraph_sites:
            continue
        match = date_range.search(paragraph)
        try:
            start, end = interval(match, paragraph)
        except ValueError:
            continue
        # Commas and semicolons separate directives. A date-only clause and
        # a named site can carry into the immediately following activity clause.
        current_sites = paragraph_sites if len(paragraph_sites) == 1 else []
        for clause in re.split(r'[,，；;]|但(?:是)?|不过', paragraph):
            own_date = date_range.search(clause)
            if own_date:
                try:
                    start, end = interval(own_date, clause)
                except ValueError:
                    continue
            clause_sites = [key for key, aliases in SITE_NAMES.items()
                            if any(alias in clause for alias in aliases)]
            if clause_sites:
                current_sites = clause_sites
            activities = [key for key, pattern in ACTIVITY_PATTERNS.items() if re.search(pattern, clause)]
            if 'boat_fishing' in activities and not re.search(r'岸钓|矶钓', clause):
                activities = [key for key in activities if key != 'shore_fishing']
            # Product names and factual prose are not permissions at all.
            if not re.search(r'允许|准许|恢复|开放|禁止|严禁|暂停|停止|关闭|不准|不可|不得|勿|谢绝', clause):
                continue
            for activity in activities:
                state = _activity_statement_state(clause, activity)
                # A within-day operating interval needs a richer time parser;
                # never inflate an 08:00-18:00 permission into a whole day.
                if re.search(r'\d{1,2}[:：时点]\d{0,2}', paragraph):
                    state = 'unresolved'
                if state == 'verified_open' and re.search(r'预计|计划|另行通知|待定|未定|(?:如|若|待|视).{0,12}(?:天气|条件|情况|评估)', paragraph):
                    state = 'unresolved'
                for site in current_sites:
                    item = {'kind': 'activity_permission', 'activity': activity,
                            'location_key': 'zhujiajian', 'site_id': site, 'state': state,
                            'value': 'closed' if state == 'verified_closed' else 'open' if state == 'verified_open' else 'unknown',
                            'valid_from': start, 'valid_to': end, 'source_url': url,
                            'published_at': published, 'published_at_time': published_time,
                            'text_excerpt': clause.strip()[:1500],
                            'validity_basis': ('unresolved_statement' if state == 'unresolved' else
                                'explicit_dated_statement' if start and end else 'unresolved_effective_interval')}
                    if start and end and state != 'unresolved':
                        evidence.append(item)
                    else:
                        events.append(item)
    if not evidence and not events:
        raise ValueError('explicit_activity_notice_not_found')
    starts = [row['valid_from'] for row in evidence if row['valid_from']]
    ends = [row['valid_to'] for row in evidence if row['valid_to']]
    return {'source': {'kind': 'activity_notice', 'title': '朱家尖旅游：活动开放与限制公告',
                       'url': url, 'published_at': published, 'observed_at': None,
                       'published_at_time': published_time,
                       'valid_from': min(starts) if starts else None,
                       'valid_to': max(ends) if ends else None, 'status': 'parsed',
                       'text_excerpt': body.strip()[:6000], 'scope': 'named_site_activity_only'},
            'activity_evidence': evidence, 'unresolved_notice_events': events}


def parse_opening_page(html_text, url=OPENING_URL):
    """Parse the dated operator schedule, without asserting beach permissions."""
    page = _Page(html_text)
    published = _published(page)
    marker = '景区营业时间调整通知'
    if not published or marker not in page.text:
        raise ValueError('operator_schedule_not_found')
    body = page.text.split(marker, 1)[1]
    # Only this known article's schedule is an evidence excerpt; no page chrome.
    body = body.split('end-->', 1)[0].split('Produced By', 1)[0][:6000]
    year = int(published[:4])
    sections = list(re.finditer(r'(\d{1,2})月(\d{1,2})日\s*[-—~～至]\s*(?:(\d{1,2})月)?(\d{1,2})日', body))
    sites = r'(?:南沙景点|东沙景点|乌石塘景区|白山景区)'
    times = re.compile(r'(' + sites + r'(?:[、,，和及\s]*' + sites + r')*)\s*(?:营业时间)?\s*(?:调整为|为)?\s*(\d{1,2})[：:](\d{2})\s*[-—~～至]\s*(\d{1,2})[：:](\d{2})')
    hours = []
    for index, section in enumerate(sections):
        m1, d1, m2, d2 = section.groups()
        valid_from = date(year, int(m1), int(d1)).isoformat()
        valid_to = date(year, int(m2 or m1), int(d2)).isoformat()
        if valid_to < valid_from:
            raise ValueError('invalid_operator_date_range')
        segment = body[section.end():sections[index + 1].start() if index + 1 < len(sections) else len(body)]
        for match in times.finditer(segment):
            site_names, h1, min1, h2, min2 = match.groups()
            if not (0 <= int(h1) <= 23 and 0 <= int(h2) <= 23 and int(min1) < 60 and int(min2) < 60):
                raise ValueError('invalid_operator_time')
            start_time, end_time = f'{int(h1):02d}:{min1}', f'{int(h2):02d}:{min2}'
            if end_time <= start_time:
                raise ValueError('invalid_operator_time_range')
            for site in re.findall(sites, site_names):
                hours.append({'site': site, 'start_time': start_time, 'end_time': end_time,
                              'valid_from': valid_from, 'valid_to': valid_to,
                              'scope': 'scenic_admission_only', 'source_url': url})
    if not hours:
        raise ValueError('operator_hours_not_found')
    source = {'kind': 'operator_schedule', 'title': '朱家尖旅游：景区营业时间调整通知', 'url': url,
              'published_at': published, 'published_at_time': _published_time(page), 'observed_at': None,
              'valid_from': min(row['valid_from'] for row in hours),
              'valid_to': max(row['valid_to'] for row in hours),
              'status': 'parsed', 'text_excerpt': (marker + body).strip()[:6000],
              'scope': 'scenic_admission_only'}
    for row in hours:
        row['site_id'] = site_id_for_name(row['site'])
        row['published_at'] = published
        row['published_at_time'] = _published_time(page)
    return {'source': source, 'opening_hours': hours}


def parse_quality_page(html_text, url=QUALITY_URL, site_name='舟山朱家尖海水浴场'):
    """Read exactly one named station, never a nearby administrative region.

    A closed/not-monitored report is a dated monitoring observation, not a
    perpetual closure order. It cannot supply measurements or activity permits.
    """
    page = _Page(html_text)
    published = _published(page)
    title = re.search(r'(20\d{2}年部分沿海城市海水浴场水质概况[（(]第\d+期[）)])', page.text)
    if not published or not title:
        raise ValueError('quality_report_not_found')
    matches = [(row, index) for row in page.rows for index, cell in enumerate(row)
               if cell == site_name]
    if len(matches) != 1:
        raise ValueError('quality_row_not_unique')
    row, index = matches[0]
    if len(row) < index + 3:
        raise ValueError('quality_row_incomplete')
    observed = re.fullmatch(r'(\d{1,2})月(\d{1,2})日', row[index + 1])
    if not observed:
        raise ValueError('quality_observation_date_missing')
    observed_at = date(int(title[1][:4]), int(observed[1]), int(observed[2])).isoformat()
    if observed_at > published:
        raise ValueError('quality_observation_after_publication')
    site_key = next((key for key, value in ACTIVITY_SITES.items()
                     if value['quality_site'] == site_name), None)
    if not site_key:
        raise ValueError('quality_site_not_registered')
    monitor_id = 'zhujiajian_bathing_water_monitor' if site_key == 'zhujiajian' else site_key
    site_match = 'regional_monitor_not_specific_beach' if site_key == 'zhujiajian' else 'exact_named_monitor'
    if '未监测' in ''.join(row[index + 2:]) or '未开展监测' in ''.join(row[index + 2:]):
        if any(cell in {'优', '良', '差', '适宜', '较适宜', '不适宜'} for cell in row[index + 3:]):
            raise ValueError('quality_not_monitored_contradicts_measurements')
        text = '；'.join(row[index:])
        return {'source': {'kind': 'bathing_water_quality', 'title': '国家海洋环境监测中心：' + title[1],
                'url': url, 'published_at': published, 'published_at_time': _published_time(page), 'observed_at': observed_at,
                'valid_from': observed_at, 'valid_to': observed_at, 'status': 'parsed',
                'text_excerpt': text, 'scope': 'bathing_water_observation_only'},
            'water_quality': {'site': site_name, 'site_id': monitor_id, 'observed_at': observed_at,
                'site_match_state': site_match, 'source_url': url, 'published_at': published,
                'published_at_time': _published_time(page),
                'valid_from': observed_at, 'valid_to': observed_at, 'measurement_state': 'not_monitored',
                'water_temperature_c': None, 'water_quality_grade': None,
                'reported_swimming_suitability': None,
                'reported_reason': text, 'scope': 'bathing_water_observation_only',
                'notice': '所列日期因浴场关闭等原因未监测；不是查询日关闭通知，也不能作为水质达标或活动许可。'}}
    if len(row) < index + 5:
        raise ValueError('quality_row_incomplete')
    temperature_text, quality, suitability = row[index + 2:index + 5]
    if quality not in {'优', '良', '差'} or suitability not in {'适宜', '较适宜', '不适宜'}:
        raise ValueError('quality_measurement_unavailable')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', temperature_text):
        raise ValueError('quality_temperature_missing')
    temperature = float(temperature_text)
    if not -2 <= temperature <= 40:
        raise ValueError('quality_temperature_out_of_range')
    note_start = page.text.find('注：')
    notes = page.text[note_start:].split('【', 1)[0][:2200] if note_start >= 0 else ''
    excerpt = f'{title[1]}；发布时间：{published}；' + '；'.join(row[index:]) + '\n' + notes
    source = {'kind': 'bathing_water_quality', 'title': '国家海洋环境监测中心：' + title[1],
              'url': url, 'published_at': published, 'published_at_time': _published_time(page), 'observed_at': observed_at,
              'valid_from': observed_at, 'valid_to': observed_at, 'status': 'parsed',
              'text_excerpt': excerpt[:6000], 'scope': 'bathing_water_observation_only'}
    quality_result = {'site': row[index], 'observed_at': observed_at,
                      'water_temperature_c': temperature, 'water_quality_grade': quality,
                      'reported_swimming_suitability': suitability, 'source_url': url,
                      'scope': 'bathing_water_observation_only',
                      'notice': '此结果仅对应所列监测日期，不代表其他日期水质、现场风浪或活动许可。'}
    quality_result.update(site_id=monitor_id, measurement_state='measured',
                          site_match_state=site_match,
                          valid_from=observed_at, valid_to=observed_at, published_at=published,
                          published_at_time=_published_time(page))
    return {'source': source, 'water_quality': quality_result}


def _valid_url(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.hostname in ALLOWED_HOSTS
            and parts.port in {None, 443} and not parts.username and not parts.password)


def _download(url):
    if not _valid_url(url):
        raise ValueError('source_url_not_allowed')
    response = requests.get(url, timeout=(4, 12), allow_redirects=False, stream=True,
                            headers={'User-Agent': 'OceanQA/1.0 official-source-reader',
                                     'Accept': 'text/html,application/xhtml+xml'})
    try:
        if response.status_code != 200 or not _valid_url(response.url):
            raise ValueError('source_http_unavailable')
        mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        if mime not in {'text/html', 'application/xhtml+xml'}:
            raise ValueError('source_content_type_not_html')
        size = response.headers.get('Content-Length')
        if size and int(size) > MAX_BYTES:
            raise ValueError('source_too_large')
        chunks, total, deadline = [], 0, time.monotonic() + 20
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_BYTES or time.monotonic() > deadline:
                raise ValueError('source_size_or_time_limit')
            chunks.append(chunk)
        content = b''.join(chunks)
        charset = re.search(r'charset\s*=\s*([\w-]+)', response.headers.get('Content-Type', ''), re.I)
        return content.decode(charset[1] if charset else 'utf-8', errors='strict')
    finally:
        response.close()


class ActivitySources:
    def __init__(self, store):
        self.store = store

    def _records(self):
        discoveries, index_sources = {}, []
        for url, kind in [(OPERATOR_INDEX_URL, 'operator_schedule'),
                          (QUALITY_INDEX_URL, 'bathing_water_quality')]:
            try:
                discoveries[kind] = discover_articles(_download(url), url, kind)
                status = 'parsed' if discoveries[kind] else 'empty'
                reason = None if discoveries[kind] else 'no_relevant_article_links'
            except (requests.RequestException, ValueError, UnicodeError, LookupError):
                discoveries[kind] = []
                status, reason = 'unavailable', 'discovery_failed'
            index_sources.append({'kind': 'official_source_index', 'url': url,
                                  'status': status, 'reason': reason,
                                  'discovered_urls': discoveries[kind]})
        candidates = [(OPENING_URL, 'operator_schedule'), (QUALITY_URL, 'bathing_water_quality')]
        for kind, urls in discoveries.items():
            candidates.extend((url, kind) for url in urls)
        candidates = list(dict.fromkeys(candidates))

        def read(item):
            url, kind = item
            try:
                html_text = _download(url)
                if kind == 'bathing_water_quality':
                    return parse_quality_page(html_text, url)
                records = []
                for parser in (parse_opening_page, parse_operator_notice):
                    try:
                        records.append(parser(html_text, url))
                    except ValueError:
                        continue
                if not records:
                    raise ValueError('operator_supported_content_missing')
                # A single source can supply both the admission schedule and
                # activity evidence; neither category grants the other.
                record = records[0]
                for other in records[1:]:
                    for key in ('activity_evidence', 'unresolved_notice_events'):
                        record.setdefault(key, []).extend(other.get(key, []))
                return record
            except (requests.RequestException, ValueError, UnicodeError, LookupError):
                return {'source': {'kind': kind, 'url': url, 'status': 'unavailable',
                                   'title': '朱家尖活动相关官方来源',
                                   'published_at': None, 'observed_at': None,
                                   'valid_from': None, 'valid_to': None,
                                   'text_excerpt': '', 'reason': 'fetch_or_parse_failed'}}
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(read, candidates))
        return records, index_sources

    def fetch(self, location_key, day, refresh=False, site_name=None, location=None, as_of=None):
        if location is not None and (location.get('coordinate_conflict') or location.get('key') != location_key):
            return self._unavailable(location_key, day, 'location_identity_or_coordinates_conflict')
        if location_key != 'zhujiajian' and location is not None:
            return self.fetch_for_location(location, day, refresh=refresh, as_of=as_of)
        result = {'state': 'unavailable', 'retrieved_at': None, 'location_key': location_key,
                  'query_date': str(day), 'sources': [], 'opening_hours': [],
                  'activity_evidence': [], 'unresolved_notice_events': [],
                  'site_id': site_id_for_name(site_name),
                  'water_quality': {'state': 'unavailable'},
                  'activity_permission': dict.fromkeys(
                      ('swimming', 'shore_fishing', 'boat_fishing', 'tidal_flat'), 'unverified')}
        try:
            query_day = date.fromisoformat(str(day)).isoformat()
        except (ValueError, TypeError):
            result['reason'] = 'invalid_query_date'
            return result
        if location_key != 'zhujiajian':
            result['reason'] = 'location_adapter_unavailable'
            return result
        now = local_now()
        try:
            cutoff = _query_as_of(query_day, now, as_of)
        except (ValueError, TypeError):
            result['reason'] = 'invalid_source_as_of'
            return result
        result['publication_as_of'] = cutoff.isoformat()
        result['publication_time_policy'] = '来源有时分时精确比较；只有日期时按日期比较，未核实当日具体发布时间，不构成精确历史快照。'
        cache_key = 'activity_sources:' + location_key
        cached = self.store.state(cache_key)
        fresh = False
        if cached and cached.get('schema') == SCHEMA:
            try:
                age = now - datetime.fromisoformat(cached['retrieved_at'])
                fresh = timedelta(0) <= age < CACHE_TTL
            except (ValueError, TypeError, KeyError):
                pass
        if refresh or not fresh:
            records, indexes = self._records()
            cached = {'schema': SCHEMA, 'retrieved_at': now.isoformat(), 'records': records,
                      'discovery': indexes}
            self.store.state(cache_key, cached)
        result['retrieved_at'] = cached['retrieved_at']
        result['cached'] = fresh and not refresh
        result['discovery'] = deepcopy(cached.get('discovery', []))
        result['discovery_scope'] = 'known_official_indexes_latest_3_articles_per_kind_not_exhaustive_web_search'
        result['discovery_complete'] = all(row.get('status') == 'parsed'
                                           for row in result['discovery']) and bool(result['discovery'])
        successes = 0
        quality_candidates = []
        for record in deepcopy(cached['records']):
            source = record['source']
            source['publication_time_precision'] = 'timestamp' if source.get('published_at_time') else 'date'
            if source['status'] != 'unavailable':
                source['published_by_query_date'] = source['published_at'] <= query_day
                source['published_by_as_of'] = _published_by(source, cutoff)
                if not source['published_by_as_of']:
                    source['status'] = 'not_published_by_as_of'
                    source['text_excerpt'] = ''
                    result['sources'].append(source)
                    continue
                successes += 1
                start, end = source['valid_from'], source['valid_to']
                source['status'] = ('current' if start <= query_day <= end else ('historical' if query_day > end else 'future')) if start and end else 'unresolved_interval'
                hours = [hour for hour in record.get('opening_hours', [])
                         if hour['valid_from'] <= query_day <= hour['valid_to']
                         and (not result['site_id'] or hour.get('site_id') == result['site_id'])]
                result['opening_hours'].extend(hours)
                for item in record.get('activity_evidence', []):
                    item['query_state'] = ('current' if item['valid_from'] <= query_day <= item['valid_to']
                                           and _published_by(item, cutoff) else 'outside_query_interval')
                    item['published_by_as_of'] = _published_by(item, cutoff)
                    if item['published_by_as_of'] and (not result['site_id'] or item.get('site_id') == result['site_id']):
                        result['activity_evidence'].append(item)
                result['unresolved_notice_events'].extend(item for item in record.get('unresolved_notice_events', [])
                    if _published_by(item, cutoff))
                if 'water_quality' in record:
                    quality = record['water_quality']
                    observed = quality['observed_at']
                    quality['state'] = 'current' if observed == query_day else ('historical' if observed < query_day else 'unavailable')
                    quality['published_by_query_date'] = source['published_by_query_date']
                    quality['published_by_as_of'] = source['published_by_as_of']
                    quality_candidates.append(quality)
            result['sources'].append(source)
        if quality_candidates:
            # A newer article can contain an older observation. Prefer a
            # matching observation day, then the latest actual observation.
            quality_candidates.sort(key=lambda row: (row['observed_at'] == query_day,
                                    row['observed_at'] <= query_day, row['observed_at']), reverse=True)
            result['water_quality'] = quality_candidates[0]
        # Resolve each activity only for an explicitly selected site. Different
        # beaches and conflicting notices never collapse to a regional permit.
        result['notice_evidence'] = result['activity_evidence']
        result['activity_evidence'] = []
        if result['discovery_complete']:
            for site in SITE_NAMES:
                for activity in result['activity_permission']:
                    matches = [row for row in result['notice_evidence'] if row['activity'] == activity
                               and row['site_id'] == site and row['query_state'] == 'current']
                    if not matches:
                        continue
                    latest = max(row['published_at'] for row in matches)
                    unresolved = [row for row in result['unresolved_notice_events']
                                  if row['site_id'] == site and row['activity'] == activity
                                  and latest <= row['published_at'] and _published_by(row, cutoff)
                                  and (not row['valid_from'] or row['valid_from'] <= query_day)]
                    if unresolved:
                        result.setdefault('unresolved_permissions', []).append({'site_id': site, 'activity': activity})
                        continue
                    current = [row for row in matches if row['published_at'] == latest]
                    states = {row['state'] for row in current}
                    if len(states) == 1:
                        result['activity_evidence'].extend(current)
                        if result['site_id'] == site:
                            result['activity_permission'][activity] = states.pop()
                    else:
                        result.setdefault('permission_conflicts', []).append({'site_id': site, 'activity': activity})
        result['state'] = 'available' if successes >= 2 and result['discovery_complete'] else ('partial' if successes else 'unavailable')
        result['permission_notice'] = '仅明确日期、活动及场地的公告可形成许可依据；未检索到公告不代表开放。'
        return result

    @staticmethod
    def _unavailable(location_key, day, reason):
        return {'state': 'unavailable', 'retrieved_at': None, 'location_key': location_key,
            'query_date': str(day), 'sources': [], 'opening_hours': [], 'activity_evidence': [],
            'notice_evidence': [], 'unresolved_notice_events': [], 'site_id': None,
            'water_quality': {'state': 'unavailable'}, 'discovery': [], 'discovery_complete': False,
            'activity_permission': dict.fromkeys(('swimming', 'shore_fishing', 'boat_fishing', 'tidal_flat'), 'unverified'),
            'reason': reason, 'permission_notice': '未取得匹配地点和日期的公告不代表开放。'}

    def _zhejiang_quality_records(self, target):
        try:
            urls = discover_articles(_download(QUALITY_INDEX_URL), QUALITY_INDEX_URL, 'bathing_water_quality')
            index_status = 'parsed' if urls else 'empty'
        except (requests.RequestException, ValueError, UnicodeError, LookupError):
            urls, index_status = [], 'unavailable'
        candidates = list(dict.fromkeys([QUALITY_URL, *urls]))

        def read(url):
            try:
                return parse_quality_page(_download(url), url, site_name=target)
            except (requests.RequestException, ValueError, UnicodeError, LookupError):
                return {'source': {'kind': 'bathing_water_quality', 'url': url,
                    'title': '国家海洋环境监测中心：' + target, 'status': 'unavailable',
                    'published_at': None, 'observed_at': None, 'valid_from': None, 'valid_to': None,
                    'text_excerpt': '', 'reason': 'named_site_fetch_or_parse_failed'}}
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(read, candidates))
        return records, [{'kind': 'official_source_index', 'url': QUALITY_INDEX_URL,
            'status': index_status, 'discovered_urls': urls, 'target_site': target}]

    def fetch_for_location(self, location, day, refresh=False, as_of=None):
        """Return only sources for a resolved location, with explicit coverage gaps.

        Zhejiang quality stations share a national official index. An unmatched
        town/coast has source leads only; a Zhujiajian record is never recycled.
        """
        key = location.get('key', 'unresolved')
        if location.get('coordinate_conflict') or location.get('needs_location') or location.get('coordinate_site_unverified'):
            return self._unavailable(key, day, 'location_identity_or_coordinates_conflict')
        if key == 'zhujiajian':
            return self.fetch(key, day, refresh=refresh, site_name=location.get('site_id'), as_of=as_of)
        result = self._unavailable(key, day, 'no_named_bathing_monitor_or_operator_adapter')
        result.update(province=location.get('province'), city=location.get('city'), county=location.get('county'),
                      site_id=location.get('site_id'))
        result['source_leads'] = [{'title': '国家海洋环境监测中心：海水浴场泳季水质周报',
            'url': QUALITY_INDEX_URL, 'scope': 'named_monitor_observations_only'},
            {'title': '浙江省气象局：天气与预警信息', 'url': 'https://zj.cma.gov.cn/',
             'scope': 'source_lead_not_machine_verified_current_warning'},
            {'title': '浙江省海洋监测预报中心', 'url': 'https://www.zjocean.org.cn/',
             'scope': 'source_lead_not_machine_verified_current_notice'}]
        registered = ACTIVITY_SITES.get(key)
        target = registered.get('quality_site') if registered else None
        # A caller cannot force a different station through an arbitrary name.
        if not target or location.get('quality_site') != target or location.get('site_id') != key:
            return result
        try:
            query_day = date.fromisoformat(str(day)).isoformat()
        except (ValueError, TypeError):
            result['reason'] = 'invalid_query_date'
            return result
        now, fresh = local_now(), False
        try:
            cutoff = _query_as_of(query_day, now, as_of)
        except (ValueError, TypeError):
            result['reason'] = 'invalid_source_as_of'
            return result
        result['publication_as_of'] = cutoff.isoformat()
        result['publication_time_policy'] = '来源有时分时精确比较；只有日期时按日期比较，未核实当日具体发布时间，不构成精确历史快照。'
        cache_key = 'activity_quality:' + key
        cached = self.store.state(cache_key)
        if cached and cached.get('schema') == SCHEMA:
            try:
                fresh = timedelta(0) <= now - datetime.fromisoformat(cached['retrieved_at']) < CACHE_TTL
            except (KeyError, ValueError, TypeError):
                pass
        if refresh or not fresh:
            records, discovery = self._zhejiang_quality_records(target)
            cached = {'schema': SCHEMA, 'retrieved_at': now.isoformat(),
                      'records': records, 'discovery': discovery}
            self.store.state(cache_key, cached)
        result.update(retrieved_at=cached['retrieved_at'], cached=fresh and not refresh,
                      discovery=deepcopy(cached.get('discovery', [])),
                      discovery_scope='known_national_quality_index_latest_3_articles_not_all_notices',
                      discovery_complete=False,
                      reason='operator_and_current_activity_permission_adapter_unavailable')
        candidates = []
        for record in deepcopy(cached['records']):
            source = record['source']
            source['publication_time_precision'] = 'timestamp' if source.get('published_at_time') else 'date'
            if source['status'] != 'unavailable':
                source['published_by_query_date'] = source['published_at'] <= query_day
                source['published_by_as_of'] = _published_by(source, cutoff)
                if not source['published_by_as_of']:
                    source['status'] = 'not_published_by_as_of'
                    source['text_excerpt'] = ''
                    result['sources'].append(source)
                    continue
                observed = source['observed_at']
                source['status'] = 'current' if observed == query_day else 'historical' if observed < query_day else 'future'
                quality = record['water_quality']
                quality['published_by_query_date'] = source['published_by_query_date']
                quality['published_by_as_of'] = source['published_by_as_of']
                quality['state'] = ('current' if observed == query_day else 'historical' if observed < query_day else 'unavailable')
                if quality.get('measurement_state') == 'not_monitored':
                    quality['state'] = 'not_monitored'
                candidates.append(quality)
            result['sources'].append(source)
        if candidates:
            candidates.sort(key=lambda row: (row['observed_at'] == query_day,
                row['observed_at'] <= query_day, row['observed_at']), reverse=True)
            result['water_quality'] = candidates[0]
            result['state'] = 'partial'
        return result
