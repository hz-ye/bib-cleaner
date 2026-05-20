import collections
import difflib
import re
import time
from dataclasses import dataclass
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

INPUT_FILE = 'reference.bib'
OUTPUT_FILE = 'output_clean.bib'
REPORT_FILE = 'cleaning_report.md'

HEADERS = {'User-Agent': 'BibCleaner/1.0 (mailto:your-email@example.com)'}

DEFAULT_URLDATE = '{Edit-your-desired-date}'

CROSSREF_SLEEP_SECONDS = 0.5

# ==========================================

FORMAL_ENTRY_TYPES = {'article', 'inproceedings', 'incollection'}
WEB_ENTRY_TYPES = {'online'}


@dataclass
class BibField:
    name: str
    value: str


@dataclass
class BibEntry:
    raw: str
    entry_type: str
    key: str
    fields: collections.OrderedDict
    start: int
    end: int
    start_line: int


def normalize_text(text):
    """Normalize text for conservative matching and duplicate detection."""
    text = re.sub(r'\\[a-zA-Z]+(\[[^\]]*\])?(\{([^{}]*)\})?', r'\3', text)
    text = re.sub(r'[{}"“”‘’`]', '', text)
    text = text.replace('\\&', '&')
    text = re.sub(r'[^a-zA-Z0-9]+', ' ', text.lower())
    return re.sub(r'\s+', ' ', text).strip()


def short_title(text):
    return text.split(':', 1)[0].strip()


def title_similarity(left, right):
    full_left = normalize_text(left)
    full_right = normalize_text(right)
    short_left = normalize_text(short_title(left))
    short_right = normalize_text(short_title(right))

    full_score = difflib.SequenceMatcher(None, full_left, full_right).ratio()
    short_score = difflib.SequenceMatcher(None, short_left, short_right).ratio()
    return max(full_score, short_score)


def line_number_at(text, index):
    return text.count('\n', 0, index) + 1


def find_matching_brace(text, open_index):
    open_char = text[open_index]
    close_char = '}' if open_char == '{' else ')'
    depth = 0
    escaped = False

    for i in range(open_index, len(text)):
        char = text[i]
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i
    return -1


def iter_bib_entries(text):
    """Yield all BibTeX blocks while preserving their original byte ranges."""
    for match in re.finditer(r'@\s*([A-Za-z]+)\s*[{(]', text):
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue

        raw = text[match.start():close_index + 1]
        entry = parse_entry(raw, match.start(), close_index + 1, line_number_at(text, match.start()))
        if entry:
            yield entry


def parse_entry(raw, start, end, start_line):
    header = re.match(r'@\s*([A-Za-z]+)\s*([{(])', raw)
    if not header:
        return None

    entry_type = header.group(1)
    if entry_type.lower() == 'string':
        return BibEntry(raw, entry_type, '', collections.OrderedDict(), start, end, start_line)

    open_index = header.end() - 1
    close_index = len(raw) - 1
    content = raw[open_index + 1:close_index]
    key, body = split_key_body(content)
    if key is None:
        return None

    return BibEntry(
        raw=raw,
        entry_type=entry_type,
        key=key.strip(),
        fields=parse_fields(body),
        start=start,
        end=end,
        start_line=start_line,
    )


def split_key_body(content):
    depth = 0
    escaped = False
    for i, char in enumerate(content):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
        elif char == ',' and depth == 0:
            return content[:i], content[i + 1:]
    return None, None


def parse_fields(body):
    fields = collections.OrderedDict()
    i = 0

    while i < len(body):
        while i < len(body) and body[i] in ' \t\r\n,':
            i += 1

        name_match = re.match(r'[A-Za-z][A-Za-z0-9_-]*', body[i:])
        if not name_match:
            i += 1
            continue

        name = name_match.group(0)
        i += len(name)

        while i < len(body) and body[i].isspace():
            i += 1
        if i >= len(body) or body[i] != '=':
            continue
        i += 1

        while i < len(body) and body[i].isspace():
            i += 1

        value, i = parse_value(body, i)
        fields[name.lower()] = BibField(name=name, value=value.strip())

    return fields


def parse_value(body, index):
    if index >= len(body):
        return '', index

    if body[index] == '{':
        end = find_matching_brace(body, index)
        if end == -1:
            return body[index + 1:].strip(), len(body)
        return body[index + 1:end], end + 1

    if body[index] == '"':
        escaped = False
        for i in range(index + 1, len(body)):
            char = body[i]
            if escaped:
                escaped = False
                continue
            if char == '\\':
                escaped = True
                continue
            if char == '"':
                return body[index + 1:i], i + 1
        return body[index + 1:].strip(), len(body)

    depth = 0
    escaped = False
    for i in range(index, len(body)):
        char = body[i]
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
        elif char == ',' and depth == 0:
            return body[index:i].strip(), i
    return body[index:].strip(), len(body)


def get_field(entry, name, default=''):
    field = entry.fields.get(name.lower())
    return field.value if field else default


def set_field(entry, name, value):
    lower = name.lower()
    if lower in entry.fields:
        entry.fields[lower].value = value
    else:
        entry.fields[lower] = BibField(name=name, value=value)


def delete_field(entry, name):
    entry.fields.pop(name.lower(), None)


def extract_arxiv_id(entry):
    candidates = [
        get_field(entry, 'eprint'),
        get_field(entry, 'journal'),
        get_field(entry, 'url'),
        get_field(entry, 'note'),
    ]
    arxiv_pattern = re.compile(
        r'(?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)'
        r'([a-z-]+(?:\.[A-Z]{2})?/\d{7}|[0-9]{4}\.[0-9]{4,5})(?:v\d+)?',
        re.IGNORECASE,
    )

    for candidate in candidates:
        match = arxiv_pattern.search(candidate)
        if match:
            return match.group(1)

    eprint = get_field(entry, 'eprint').strip()
    if re.fullmatch(r'[a-z-]+(?:\.[A-Z]{2})?/\d{7}|[0-9]{4}\.[0-9]{4,5}', eprint, re.IGNORECASE):
        return eprint
    return ''


def normalize_author_family(author_field):
    if not author_field:
        return ''
    first_author = re.split(r'\s+and\s+', author_field, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if ',' in first_author:
        family = first_author.split(',', 1)[0]
    else:
        family = first_author.split()[-1] if first_author.split() else ''
    return normalize_text(family)


def crossref_year(item):
    for key in ['published-print', 'published-online', 'issued', 'created']:
        date_parts = item.get(key, {}).get('date-parts', [])
        if date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return ''


def score_crossref_item(entry, item):
    entry_title = get_field(entry, 'title')
    returned_titles = item.get('title') or []
    returned_title = returned_titles[0] if returned_titles else ''
    title_score = title_similarity(entry_title, returned_title)

    entry_year = get_field(entry, 'year').strip()
    item_year = crossref_year(item)
    year_match = bool(entry_year and item_year and entry_year == item_year)

    entry_family = normalize_author_family(get_field(entry, 'author'))
    item_authors = item.get('author') or []
    item_family = normalize_text(item_authors[0].get('family', '')) if item_authors else ''
    author_match = bool(entry_family and item_family and entry_family == item_family)

    score = title_score
    if year_match:
        score += 0.04
    elif entry_year and item_year:
        score -= 0.08
    if author_match:
        score += 0.04
    elif entry_family and item_family:
        score -= 0.04

    return {
        'doi': item.get('DOI', ''),
        'title': returned_titles[0] if returned_titles else '',
        'year': item_year,
        'title_score': title_score,
        'score': score,
        'year_match': year_match,
        'author_match': author_match,
    }


def get_doi_from_crossref(entry):
    """Query Crossref and return a high-confidence DOI plus diagnostics."""
    if requests is None:
        return '', {'reason': 'requests is not installed', 'candidates': []}

    title = get_field(entry, 'title')
    url = 'https://api.crossref.org/works'
    params = {
        'query.bibliographic': title,
        'select': 'DOI,title,author,issued,published-print,published-online,created',
        'rows': 5,
    }

    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if response.status_code != 200:
            return '', {'reason': f'Crossref HTTP {response.status_code}', 'candidates': []}

        data = response.json()
        items = data.get('message', {}).get('items', [])
        scored = [score_crossref_item(entry, item) for item in items if item.get('DOI')]
        scored.sort(key=lambda item: item['score'], reverse=True)
        if not scored:
            return '', {'reason': 'no DOI candidates', 'candidates': []}

        best = scored[0]
        high_confidence = (
            best['title_score'] >= 0.92
            and best['score'] >= 0.92
            and (best['year_match'] or not get_field(entry, 'year'))
        )
        if high_confidence:
            return best['doi'], {'reason': 'high confidence', 'candidates': scored}
        return '', {'reason': 'low confidence', 'candidates': scored}

    except Exception as exc:
        return '', {'reason': f'network error: {exc}', 'candidates': []}


def render_entry(entry):
    lines = [f'@{entry.entry_type}{{{entry.key},']
    fields = list(entry.fields.values())

    for index, field in enumerate(fields):
        suffix = ',' if index < len(fields) - 1 else ''
        value = field.value
        lines.append(f'  {field.name}={{{value}}}{suffix}')

    lines.append('}')
    return '\n'.join(lines)


def entry_signature(entry):
    return (
        normalize_text(get_field(entry, 'title')),
        normalize_text(get_field(entry, 'year')),
    )


def find_duplicate_reports(entries):
    key_map = collections.defaultdict(list)
    signature_map = collections.defaultdict(list)

    for entry in entries:
        if entry.entry_type.lower() == 'string':
            continue
        key_map[entry.key].append(entry)
        signature = entry_signature(entry)
        if signature[0] and signature[1]:
            signature_map[signature].append(entry)

    duplicate_keys = {key: items for key, items in key_map.items() if len(items) > 1}
    duplicate_signatures = {
        signature: items
        for signature, items in signature_map.items()
        if len(items) > 1
    }
    return duplicate_keys, duplicate_signatures


def detect_url_risks(entry):
    url = get_field(entry, 'url')
    if not url:
        return []

    risks = []
    if re.search(r'\s', url):
        risks.append('URL contains whitespace')
    return risks


def process_entry(entry, report):
    if entry.entry_type.lower() == 'string':
        return entry.raw

    original_type = entry.entry_type
    original_fields = [(key, field.value) for key, field in entry.fields.items()]
    changes = []

    arxiv_id = extract_arxiv_id(entry)
    if arxiv_id:
        entry.entry_type = 'misc'
        set_field(entry, 'eprint', arxiv_id)
        set_field(entry, 'archivePrefix', 'arXiv')
        delete_field(entry, 'journal')
        delete_field(entry, 'url')
        changes.append(f'arXiv normalized with eprint {arxiv_id}')
        report['arxiv'].append(entry)

    entry_type_lower = entry.entry_type.lower()
    if not arxiv_id and (
        entry_type_lower in WEB_ENTRY_TYPES
        or (entry_type_lower == 'misc' and get_field(entry, 'url'))
    ):
        had_lastaccessed = 'lastaccessed' in entry.fields
        delete_field(entry, 'lastaccessed')
        set_field(entry, 'urldate', DEFAULT_URLDATE)
        if had_lastaccessed:
            changes.append(f'lastaccessed replaced by urldate {DEFAULT_URLDATE}')
        else:
            changes.append(f'urldate set to {DEFAULT_URLDATE}')
        report['web_dates'].append(entry)

    for risk in detect_url_risks(entry):
        report['url_risks'].append((entry, risk, get_field(entry, 'url')))

    if (
        not arxiv_id
        and entry_type_lower in FORMAL_ENTRY_TYPES
        and 'doi' not in entry.fields
        and get_field(entry, 'title')
    ):
        print(f"  -> [API] Crossref DOI lookup: {entry.key}")
        doi, diagnostic = get_doi_from_crossref(entry)
        if doi:
            set_field(entry, 'doi', doi)
            changes.append(f'DOI added: {doi}')
            report['doi_added'].append((entry, doi))
            print(f"  -> [OK] DOI: {doi}")
        else:
            report['doi_unresolved'].append((entry, diagnostic))
            print(f"  -> [SKIP] DOI not added: {diagnostic['reason']}")
        time.sleep(CROSSREF_SLEEP_SECONDS)

    new_fields = [(key, field.value) for key, field in entry.fields.items()]
    if original_type != entry.entry_type or original_fields != new_fields:
        report['changed'].append((entry, changes))
        return render_entry(entry)
    return entry.raw


def process_bib_text(text):
    entries = list(iter_bib_entries(text))
    duplicate_keys, duplicate_signatures = find_duplicate_reports(entries)
    report = {
        'started_at': datetime.now().isoformat(timespec='seconds'),
        'entries': [entry for entry in entries if entry.entry_type.lower() != 'string'],
        'changed': [],
        'arxiv': [],
        'web_dates': [],
        'doi_added': [],
        'doi_unresolved': [],
        'url_risks': [],
        'duplicate_keys': duplicate_keys,
        'duplicate_signatures': duplicate_signatures,
    }

    output = []
    cursor = 0
    total = len(report['entries'])
    processed = 0

    for entry in entries:
        output.append(text[cursor:entry.start])
        if entry.entry_type.lower() != 'string':
            processed += 1
            print(f'[{processed}/{total}] Processing: {entry.key}')
        output.append(process_entry(entry, report))
        cursor = entry.end

    output.append(text[cursor:])
    return ''.join(output), report


def format_candidates(candidates, limit=3):
    lines = []
    for candidate in candidates[:limit]:
        lines.append(
            f"- `{candidate['doi']}` | title_score={candidate['title_score']:.3f} "
            f"| score={candidate['score']:.3f} | year={candidate['year']} "
            f"| title={candidate['title']}"
        )
    return '\n'.join(lines) if lines else '- No candidates'


def write_report(report):
    lines = [
        '# BibTeX Cleaning Report',
        '',
        f"- Input file: `{INPUT_FILE}`",
        f"- Output file: `{OUTPUT_FILE}`",
        f"- Generated at: `{report['started_at']}`",
        f"- Entries scanned: `{len(report['entries'])}`",
        f"- Entries changed: `{len(report['changed'])}`",
        f"- arXiv entries normalized: `{len(report['arxiv'])}`",
        f"- Web urldate updates: `{len(report['web_dates'])}`",
        f"- DOI added: `{len(report['doi_added'])}`",
        f"- DOI unresolved or low confidence: `{len(report['doi_unresolved'])}`",
        f"- URL risks: `{len(report['url_risks'])}`",
        f"- Duplicate keys: `{len(report['duplicate_keys'])}`",
        f"- Duplicate title/year groups: `{len(report['duplicate_signatures'])}`",
        '',
    ]

    lines.extend(['## Changed Entries', ''])
    if report['changed']:
        for entry, changes in report['changed']:
            lines.append(f"- `{entry.key}` at line {entry.start_line}: {', '.join(changes)}")
    else:
        lines.append('- None')
    lines.append('')

    lines.extend(['## URL Risks', ''])
    if report['url_risks']:
        for entry, risk, url in report['url_risks']:
            lines.append(f"- `{entry.key}` at line {entry.start_line}: {risk}: `{url}`")
    else:
        lines.append('- None')
    lines.append('')

    lines.extend(['## Duplicate Keys', ''])
    if report['duplicate_keys']:
        for key, entries in report['duplicate_keys'].items():
            lines.append(f"### `{key}`")
            lines.append('')
            for entry in entries:
                lines.append(f"- Line {entry.start_line}")
                lines.append('')
                lines.append('```bibtex')
                lines.append(entry.raw.strip())
                lines.append('```')
                lines.append('')
    else:
        lines.append('- None')
    lines.append('')

    lines.extend(['## Duplicate Title And Year', ''])
    if report['duplicate_signatures']:
        for (title, year), entries in report['duplicate_signatures'].items():
            lines.append(f"### `{year}` | `{title}`")
            lines.append('')
            for entry in entries:
                lines.append(f"- `{entry.key}` at line {entry.start_line}")
            lines.append('')
    else:
        lines.append('- None')
    lines.append('')

    lines.extend(['## DOI Added', ''])
    if report['doi_added']:
        for entry, doi in report['doi_added']:
            lines.append(f"- `{entry.key}` at line {entry.start_line}: `{doi}`")
    else:
        lines.append('- None')
    lines.append('')

    lines.extend(['## DOI Unresolved Or Low Confidence', ''])
    if report['doi_unresolved']:
        for entry, diagnostic in report['doi_unresolved']:
            lines.append(f"### `{entry.key}` at line {entry.start_line}")
            lines.append('')
            lines.append(f"- Reason: {diagnostic['reason']}")
            lines.append(f"- Title: {get_field(entry, 'title')}")
            lines.append('')
            lines.append(format_candidates(diagnostic.get('candidates', [])))
            lines.append('')
    else:
        lines.append('- None')
    lines.append('')

    with open(REPORT_FILE, 'w', encoding='utf-8') as report_file:
        report_file.write('\n'.join(lines))


def main():
    print('Reading BibTeX file...')
    with open(INPUT_FILE, 'r', encoding='utf-8') as bibtex_file:
        text = bibtex_file.read()

    cleaned_text, report = process_bib_text(text)

    print('\nCleaning complete. Writing output file and report...')
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as bibtex_file:
        bibtex_file.write(cleaned_text)
    write_report(report)

    print(f"Done. Output file: {OUTPUT_FILE}")
    print(f"Report file: {REPORT_FILE}")
    print(
        'Summary: '
        f"entries={len(report['entries'])}, "
        f"changed={len(report['changed'])}, "
        f"arxiv={len(report['arxiv'])}, "
        f"web_dates={len(report['web_dates'])}, "
        f"doi_added={len(report['doi_added'])}, "
        f"doi_unresolved={len(report['doi_unresolved'])}, "
        f"url_risks={len(report['url_risks'])}, "
        f"duplicate_keys={len(report['duplicate_keys'])}, "
        f"duplicate_title_year={len(report['duplicate_signatures'])}"
    )


if __name__ == '__main__':
    main()
