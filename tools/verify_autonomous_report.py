"""Check local report integrity without modifying answers or runtime sources."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'tmp/autonomous_v172_20261018'
RUN = ROOT / 'tmp/qa_full160_phase172_20261018_final'
REPORT = ROOT / 'exports/自动迭代_160题实测与文献改造报告_20261018.html'


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.ids = []
        self.actual = []
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'article' and a.get('id'):
            self.ids.append(a['id'])
        if tag == 'pre' and a.get('class') == 'actual':
            self.active, self.parts = True, []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'pre' and self.active:
            self.actual.append(''.join(self.parts))
            self.active = False


def load(path):
    return json.loads(path.read_text(encoding='utf8'))


def nonempty_sensitive_keys(value, path='$'):
    """Report key paths only, never any potentially sensitive values."""
    found = []
    sensitive = re.compile(r'^(?:api_?key|access_?key|secret(?:_?key)?|password|authorization|bearer_?token|cookie|provider_?token)$', re.I)
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + '.' + str(key)
            if sensitive.fullmatch(str(key)) and child not in (None, '', False, [], {}):
                found.append(child_path)
            found.extend(nonempty_sensitive_keys(child, child_path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(nonempty_sensitive_keys(child, path + '[' + str(i) + ']'))
    return found


def main():
    parsed = ReportParser()
    parsed.feed(REPORT.read_text(encoding='utf8'))
    manifest = load(RUN / 'manifest.json')
    expected_ids = {row['id'] for row in manifest['items']}
    assert len(parsed.ids) == len(set(parsed.ids)) == 160
    assert set(parsed.ids) == expected_ids
    bad_links = []
    local = set()
    for link in parsed.links:
        url = urlsplit(link)
        if url.scheme in ('https', 'http') or not url.path:
            continue
        assert not url.scheme, 'Unexpected URI scheme in report'
        path = (REPORT.parent / unquote(url.path)).resolve()
        local.add(str(path))
        if not path.is_file():
            bad_links.append(link)
    assert not bad_links, bad_links
    actual_counts = Counter(parsed.actual)
    expected = []
    sensitive_paths = []
    for folder in (RUN, ROOT / 'tmp/qa_full160_phase164_20261018_final'):
        data = load(folder / 'answers.json')
        expected.extend(row['reply']['answer'] for row in data['items'])
        sensitive_paths.extend(nonempty_sensitive_keys(data, folder.name))
    assert not (Counter(expected) - actual_counts), 'Answer text or multiplicity lost'
    assert not sensitive_paths, 'Sensitive configuration key present in export'
    report_check = load(WORK / 'report_verification.json')
    assert report_check['sha256'] == hashlib.sha256(REPORT.read_bytes()).hexdigest()
    report_check.update({
        'unique_question_ids': len(parsed.ids),
        'all_manifest_ids_present': True,
        'existing_local_link_targets': len(local),
        'broken_local_links': bad_links,
        'answer_multiplicity_check': True,
    })
    (WORK / 'report_verification.json').write_text(json.dumps(report_check, ensure_ascii=False, indent=2), encoding='utf8')
    key_check = {
        'nonempty_sensitive_key_paths': sensitive_paths,
        'note': '仅检查导出对象中的敏感配置键，不声称识别所有自由文本秘密。未输出任何密钥值。',
    }
    (WORK / 'export_key_audit.json').write_text(json.dumps(key_check, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report_check, ensure_ascii=False))


if __name__ == '__main__':
    main()
