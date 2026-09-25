"""Reading sources.csv, and fetching what it points at, in one place.

Each row is: name, url, maintainer, kind

  name        unique, lowercase; becomes sources/external/<name>.json
  url         http(s) link to the raw JSON
  maintainer  the source's home page, shown as its "Report issues" link
  kind        'collection' for someone's maintained list of apps, 'app' for a
              single app published by its own author. Blank means collection,
              so the original three-column rows still read correctly.

The build trusts the two kinds differently. An app source outranks every
collection when the same app appears in both, since its author maintains that
one app and knows it best. In exchange it may only carry a handful of
templates, and if it stops resolving the build carries on without it.
"""
import csv
import os
import re
import time
from typing import NamedTuple
from urllib.parse import urlparse

from log import get_logger

log = get_logger()

SOURCES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sources.csv')

COLLECTION = 'collection'
APP = 'app'
KINDS = (COLLECTION, APP)

# A source name becomes a file name, so keep it to something unambiguous
NAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]*$')

# An app may reasonably ship a container and a stack, or a plain build and a
# GPU one. Much past that is a collection, and belongs in one.
MAX_APP_TEMPLATES = 5

class Source(NamedTuple):
  """One usable row of sources.csv"""
  name: str
  url: str
  maintainer: str
  kind: str

  @property
  def filename(self):
    """Where download.py saves it, and the name combine.py ranks it by"""
    return f'{self.name}.json'

  @property
  def is_app(self):
    return self.kind == APP

def rows(path=SOURCES_CSV):
  """Every non-blank row, as (line number, stripped cells)"""
  with open(path, newline='') as file:
    for line, row in enumerate(csv.reader(file), start=1):
      cells = [cell.strip() for cell in row]
      if any(cells):
        yield line, cells

def row_problems(cells):
  """Everything wrong with a row, as (level, message) pairs. Any problem at all
  means load() skips the row; the level only decides how loudly to report it."""
  if len(cells) < 2 or not cells[0] or not cells[1]:
    return [('warning', f'malformed row, would be skipped: {cells}')]
  problems = []
  if not NAME_PATTERN.match(cells[0]):
    problems.append(('error', f'name {cells[0]!r} becomes a file name, so it may only hold '
                              "lowercase letters, digits, '_' and '-'"))
  if urlparse(cells[1]).scheme not in ('http', 'https'):
    problems.append(('error', f'{cells[0]} has a non-http(s) url: {cells[1]}'))
  kind = cells[3] if len(cells) > 3 else ''
  if kind and kind.lower() not in KINDS:
    problems.append(('error', f'{cells[0]} has kind {kind!r}, expected '
                              f'{" or ".join(KINDS)}'))
  if len(cells) > 4 and any(cells[4:]):
    problems.append(('error', f'{cells[0]} has {len(cells)} columns, expected at most 4 '
                              '(name, url, maintainer, kind)'))
  return problems

def parse(cells):
  """A Source from a row already known to be well-formed"""
  kind = cells[3].lower() if len(cells) > 3 and cells[3] else COLLECTION
  maintainer = cells[2] if len(cells) > 2 else ''
  return Source(cells[0], cells[1], maintainer, kind)

def load(path=SOURCES_CSV):
  """Every usable source, in listed order. Rows with problems are skipped here
  and reported by validate_sources.py, which is what gates a PR."""
  sources, seen = [], set()
  for line, cells in rows(path):
    problems = row_problems(cells)
    if problems:
      for _, message in problems:
        log.warning(f'Skipping sources.csv:{line}: {message}')
      continue
    if cells[0] in seen:
      log.warning(f'Skipping sources.csv:{line}: duplicate source {cells[0]!r}')
      continue
    seen.add(cells[0])
    sources.append(parse(cells))
  return sources

def fetch_json(url, attempts=3):
  """Fetch and parse JSON from a url, retrying a couple of times on a blip.
  Returns (payload, error message), of which exactly one is set."""
  import requests  # only the fetching path needs it, so keep it off the parsers
  for attempt in range(attempts):
    try:
      response = requests.get(url, timeout=30)
      response.raise_for_status()
      return response.json(), None
    except (requests.RequestException, ValueError) as err:
      if attempt == attempts - 1:
        return None, str(err)
      time.sleep(2 ** attempt)

def templates_in(payload, allow_bare=False):
  """The template list inside a downloaded source, whatever shape it arrived in,
  or None if there isn't one. `allow_bare` accepts a single unwrapped template
  object, which is the natural way to publish just the one app."""
  if isinstance(payload, list):
    return payload
  if isinstance(payload, dict):
    if isinstance(payload.get('templates'), list):
      return payload['templates']
    if allow_bare and isinstance(payload.get('title'), str):
      return [payload]
  return None
