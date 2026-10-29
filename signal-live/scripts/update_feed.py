#!/usr/bin/env python3
"""SIGNAL public RSS aggregator and deliberately conservative headline signal.
No paid services, keys, AI, or claim-verification. Standard library only.
Google News links often redirect through its own service.
"""
from __future__ import annotations
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / 'data' / 'feed.json'
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
ALLOWED = {
    'Reuters', 'Associated Press', 'AP News', 'BBC News', 'BBC', 'Al Jazeera',
    'Al Jazeera English', 'France 24', 'The Guardian', 'Financial Times',
    'NPR', 'PBS News', 'Deutsche Welle', 'DW', 'UN News', 'United Nations',
    'The Washington Post', 'The New York Times', 'CNN', 'CBC News',
    'The Times of Israel', 'Axios', 'Politico', 'Bloomberg', 'The Wall Street Journal',
    'Middle East Eye', 'The Independent', 'Sky News',
}
# Each feed is a publisher-reported headline search, not a fact-check or endorsement.
QUERIES = {
    'military': '(Iran OR Iranian) (Israel OR US OR Houthi OR Saudi) (strike OR attack OR missile OR tanker OR Hormuz OR military) when:3d',
    'civilian': '(Iran OR Yemen OR Israel OR Saudi) (civilians OR killed OR humanitarian OR hospital OR displacement OR aid) when:3d',
    'diplomacy': '(Iran OR Israel) (ceasefire OR talks OR negotiations OR mediation OR diplomatic OR agreement) when:3d',
    'rhetoric': '(Iran OR Israel) (Armageddon OR Mahdi OR Gog OR Magog OR apocalypse OR prophecy OR end-times OR martyrdom) when:14d',
}
RELEVANCE = re.compile(r'\b(iran|iranian|israel|israeli|houthi|yemen|saudi|hormuz|tehran|tel aviv|red sea|gulf states|gaza|middle east|washington)\b', re.I)
RELIGIOUS = re.compile(r'\b(armageddon|mahdi|gog|magog|apocalypt\w*|prophe\w*|end.times|messiah|martyr\w*|religious war)\b',re.I)
ACTIVE = re.compile(r'\b(struck|strikes|hit|attacked|attack|killed|dead|bombed|intercepted|launched|fired|downed|injured|wounded|sank|exploded)\b',re.I)
UNCERTAIN = re.compile(r'\b(could|might|may|would|warns?|threatens?|risk|fears?|prepares?|plans?|potential|reportedly|alleged|claims?|if|possible|urges?)\b',re.I)


def parse_date(v: str) -> dt.datetime | None:
    try:
        d = email.utils.parsedate_to_datetime(v)
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def fetch_rss(query: str) -> bytes:
    args = urllib.parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
    url = 'https://news.google.com/rss/search?' + args
    req = urllib.request.Request(url, headers={'User-Agent':'SignalConflictWatch/2.0 (+https://github.com/; personal news dashboard)', 'Accept':'application/rss+xml, application/xml, text/xml'})
    with urllib.request.urlopen(req, timeout=18) as r:
        if int(r.status) != 200: raise RuntimeError(f'RSS HTTP {r.status}')
        return r.read(2_000_000)


def parse_rss(raw: bytes, category: str) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    for node in root.findall('.//channel/item'):
        title = clean_text(node.findtext('title') or '')
        source_node = node.find('source')
        source = clean_text(source_node.text or '') if source_node is not None else ''
        if not source and ' - ' in title:
            title, source = title.rsplit(' - ', 1)
        elif source and title.endswith(' - ' + source):
            title = title[:-(len(source)+3)]
        link = (node.findtext('link') or '').strip()
        date = parse_date(node.findtext('pubDate') or '')
        if not title or not date or not source or source not in ALLOWED: continue
        if (NOW - date).total_seconds() > (14 if category=='rhetoric' else 3)*86400: continue
        if date > NOW + dt.timedelta(hours=2): continue
        if not RELEVANCE.search(title): continue
        if category=='rhetoric' and not RELIGIOUS.search(title): continue
        if not link.startswith('https://'): continue
        entry_id = hashlib.sha256((source+'|'+title.lower()).encode()).hexdigest()[:14]
        items.append({
            'id':entry_id, 'date':date.date().isoformat(),
            'published_at':date.isoformat(timespec='seconds'),
            'category':category, 'status':'reported',
            'title':title[:170], 'publisher':source[:65],
            'url':link[:1500],
            'summary':'Headline indexed from '+source+'. Read the linked report to assess the underlying claim; this feed does not independently verify the event.',
            'provenance':'automated_rss'
        })
    return items


def score_signal(entries: list[dict]) -> tuple[int,str]:
    """Conservative provisional headline-volume signal, NOT factual severity measurement.
    Cannot automatically assign 9/10 or 10/10; those need an editor's assessment.
    """
    cutoff = NOW-dt.timedelta(hours=72)
    recent = [e for e in entries if dt.datetime.fromisoformat(e['published_at'])>=cutoff]
    military = [e for e in recent if e['category']=='military']
    active = [e for e in military if ACTIVE.search(e['title']) and not UNCERTAIN.search(e['title'])]
    civilian = [e for e in recent if e['category']=='civilian' and ACTIVE.search(e['title']) and not UNCERTAIN.search(e['title'])]
    distinct = len(set(e['publisher'] for e in recent))
    # Quantity-based proxy, deliberately not a threat prediction.
    value = 3
    if len(military)>=3 and distinct>=2: value = 4
    if len(active)>=3 and distinct>=2: value = 5
    if len(active)>=7 and distinct>=3: value = 6
    if len(active)>=12 and len(civilian)>=3 and distinct>=4: value = 7
    if len(active)>=18 and len(civilian)>=6 and distinct>=5: value = 8
    reason = (f'Automated headline signal: {len(military)} military-related reports, '
              f'{len(active)} using incident language and {len(civilian)} civilian-impact headlines '
              f'from {distinct} publishers in the past 72 hours. Does not establish incidents or verify casualties.')
    return value,reason


def main() -> int:
    prior = {}
    if FILE.exists():
        try: prior = json.loads(FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError): pass
    all_entries = []
    ok = 0
    for category,query in QUERIES.items():
        try:
            entries = parse_rss(fetch_rss(query),category)
            all_entries.extend(entries[:35]);ok+=1
            print(f'{category}: {len(entries)} filtered headlines')
        except (OSError,ValueError,ET.ParseError,RuntimeError) as e:
            print(f'ERROR {category}: {e}', file=sys.stderr)
    if ok == 0 or not all_entries:
        # Never overwrite a previous successful feed with empty data or show fake freshness.
        print('No usable headlines from RSS; preserving existing data.', file=sys.stderr)
        return 1
    unique={}
    for item in sorted(all_entries,key=lambda e:e['published_at'],reverse=True):
        unique.setdefault((item['publisher'].lower(),re.sub(r'\W+','',item['title'].lower())),item)
    entries=list(unique.values())[:80]
    value,reason=score_signal(entries)
    current = {'date':NOW.date().isoformat(),'value':value,'reason':reason,'at':NOW.isoformat(timespec='seconds')}
    history=[s for s in prior.get('history',[]) if isinstance(s,dict) and str(s.get('date','')) and isinstance(s.get('value'),int)]
    if history and history[-1]['date']==current['date']:
        history[-1]=current
    else:
        history.append(current)
    history=history[-90:]
    payload = {
        'schema':2, 'source':'Google News RSS publisher headlines',
        'generated_at': NOW.isoformat(timespec='seconds'),
        'refresh_interval_hours':6,
        'status':'ok' if ok==len(QUERIES) else 'partial',
        'successful_categories':ok,
        'headline_count':len(entries),
        'verification':'Publisher-attributed headlines only; not independently fact-checked.',
        'score':{'value':value,'date':current['date'],'reason':reason,'method':'automated_provisional_headline_indicator'},
        'history':history,
        'headlines':entries,
    }
    FILE.parent.mkdir(parents=True,exist_ok=True)
    FILE.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Wrote {len(entries)} headlines; provisional indicator {value}/10')
    return 0

if __name__=='__main__':
    sys.exit(main())
