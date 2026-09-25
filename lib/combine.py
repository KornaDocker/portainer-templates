import json
import os
import re
import string
import sys
from collections import Counter

import jsonschema

import sources_list
from log import get_logger, banner

log = get_logger()

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SOURCES_DIR = os.path.join(BASE_DIR, 'sources')

with open(os.path.join(BASE_DIR, 'Schema.json')) as f:
  SCHEMA = json.load(f)
FORMAT_CHECKER = jsonschema.FormatChecker()
TEMPLATE_KEYS = set(SCHEMA['properties']['templates']['items']['properties'])
ITEM_VALIDATOR = jsonschema.Draft7Validator(SCHEMA['properties']['templates']['items'],
                                            format_checker=FORMAT_CHECKER)

TYPE_LABELS = {1: 'container', 2: 'swarm', 3: 'stack', 4: 'edge'}

def normalize_string(original, lowercase=True):
  normalized = original.translate(str.maketrans('', '', string.punctuation)).replace(' ', '')
  return normalized.lower() if lowercase else normalized.capitalize()

CATEGORY_ACRONYMS = {'ai', 'vpn', 'iot', 'nas', 'dns', 'ci', 'cd', 'tv', 'os', 'ip', 'sql',
                     'api', 'cms', 'ftp', 'rss', 'cctv', 'llm', 'osint', 'pdf', '3d'}

def normalize_category(c):
  """Split a category into words and title-case it for display"""
  if not isinstance(c, str):
    return ''
  # Some sources namespace their categories, and the subtype is the useful half
  c = c.rsplit(':', 1)[-1].strip() or c
  words = [w for w in re.split(r'[^0-9a-zA-Z]+', c) if w]
  label = ' '.join(w.upper() if w.lower() in CATEGORY_ACRONYMS else w.capitalize()
                   for w in words)
  return 'edge' if label == 'Edge' else label

def template_score(t):
  """Score a template's completeness. Higher is more complete."""
  score = len(t)  # number of top-level keys
  score += len(t.get('env', []))
  score += len(t.get('volumes', []))
  score += len(t.get('ports', []))
  return score

def template_rank(t, pinned=None):
  """Sort key for choosing between duplicates, higher wins: pinned source, then source
  priority, then completeness."""
  return (t['_source'] == pinned, -t['_priority'], template_score(t))

def source_priority(sources):
  """File name -> dedup priority, where lower wins. An app source outranks every
  collection, because its author maintains that one app and knows it best.
  Within each kind, the order they're listed in sources.csv decides."""
  ordered = [s for s in sources if s.is_app] + [s for s in sources if not s.is_app]
  return {s.filename: i for i, s in enumerate(ordered, start=1)}

def load_overrides():
  """Per-template fixes from overrides.json, keyed by (normalized title, type)."""
  path = os.path.join(BASE_DIR, 'overrides.json')
  if not os.path.isfile(path):
    return {}
  with open(path) as f:
    try:
      entries = json.load(f).get('overrides', [])
    except json.decoder.JSONDecodeError as err:
      log.error(f'Refusing to build: overrides.json is not valid JSON ({err})')
      sys.exit(1)
  return {(normalize_string(e['title']), e.get('type', 1)): e for e in entries
          if isinstance(e, dict) and isinstance(e.get('title'), str) and e['title'].strip()}

def pinned_source(overrides, key):
  """The source file name an override pins this (title, type) to, if any."""
  prefer = overrides.get(key, {}).get('prefer')
  return f'{prefer}.json' if prefer else None

def load_sources(sources):
  """Load and merge all template JSON files from sources/local/ and sources/external/."""
  rank = source_priority(sources)
  templates = []
  local_dir = os.path.join(SOURCES_DIR, 'local')
  external_dir = os.path.join(SOURCES_DIR, 'external')
  for is_local, d in [(True, local_dir), (False, external_dir)]:
    if not os.path.isdir(d):
      continue
    for file in sorted(os.listdir(d)):
      file_path = os.path.join(d, file)
      if not (os.path.isfile(file_path) and file.endswith('.json')):
        continue
      with open(file_path) as f:
        try:
          source_templates = json.load(f)['templates']
        except (json.decoder.JSONDecodeError, KeyError) as err:
          log.warning(f'Skipping source due to error: {f.name} ({err})')
          continue
      source_templates = [t for t in source_templates if isinstance(t, dict)]
      # Dedup priority: local files first, then external sources in sources.csv order
      priority = 0 if is_local else rank.get(file, len(rank) + 1)
      for t in source_templates:
        t['_source'] = file
        t['_priority'] = priority
      templates += source_templates
  return templates

VALID_ENV_KEYS = {'name', 'label', 'description', 'default', 'preset', 'select'}

def is_uri(value):
  return isinstance(value, str) and FORMAT_CHECKER.conforms(value, 'uri')

STACKFILE_URL_PATTERNS = [
  (r'https://raw\.githubusercontent\.com/([^/]+/[^/]+)/[^/]+/(.+)', 'github.com'),
  (r'https://github\.com/([^/]+/[^/]+)/(?:blob|raw)/[^/]+/(.+)', 'github.com'),
  (r'https://gitlab\.com/(.+?)/-/(?:blob|raw)/[^/]+/(.+)', 'gitlab.com'),
]

def normalize_template(t):
  t.setdefault('type', 1)

  # Merge singular 'category' into 'categories'
  categories = t.get('categories', [])
  if isinstance(categories, str):
    categories = [categories]
  elif not isinstance(categories, list):
    categories = []
  if 'category' in t:
    extra = t.pop('category')
    extra = extra if isinstance(extra, list) else [extra]
    categories += extra
  if categories:
    t['categories'] = list(dict.fromkeys(categories))
  elif 'categories' in t:
    del t['categories']

  # Convert legacy v2 edge templates (type 4 with a stackfile URL) to compose stacks
  if t.get('type') == 4 and isinstance(t.get('stackfile'), str):
    sf = t.pop('stackfile')
    for pattern, host in STACKFILE_URL_PATTERNS:
      m = re.match(pattern, sf)
      if m:
        t['type'] = 3
        t['repository'] = {'url': f'https://{host}/{m[1]}', 'stackfile': m[2]}
        t.setdefault('categories', ['edge'])
        break
    else:
      if sf.startswith('http'):
        raise ValueError(f'unrecognized stackfile URL: {sf}')
      t['stackFile'] = sf  # inline stack content under a miscased key

  # Fix env vars
  if 'env' in t:
    if not isinstance(t['env'], list):
      t['env'] = [t['env']]
    cleaned_env = []
    for env in t['env']:
      if isinstance(env, str) and '=' in env:
        name, _, default = env.partition('=')
        cleaned_env.append({'name': name, 'default': default})
        continue
      # Filter malformed entries (entire templates nested in env arrays)
      if not isinstance(env, dict) or not isinstance(env.get('name'), str) or not env['name'] \
         or any(k in env for k in ('categories', 'repository', 'logo')):
        continue
      # Convert non-standard 'set' to 'default' + 'preset'
      if 'set' in env and 'default' not in env:
        env['default'] = env.pop('set')
        env.setdefault('preset', True)
      elif 'set' in env:
        del env['set']
      if 'default' in env and not isinstance(env['default'], str):
        env['default'] = json.dumps(env['default'])
      if 'preset' in env and not isinstance(env['preset'], bool):
        env['preset'] = str(env['preset']).lower() in ('true', '1')
      if 'select' in env:
        env['select'] = [
          {'text': str(o.get('text') or o['value']), 'value': str(o['value']),
           **({'default': o['default']} if isinstance(o.get('default'), bool) else {})}
          for o in env['select'] if isinstance(o, dict) and 'value' in o]
      cleaned_env.append({k: v for k, v in env.items() if k in VALID_ENV_KEYS})
    t['env'] = cleaned_env

  # Drop malformed leading ':' from port mappings (e.g. ':80/tcp')
  if 'ports' in t:
    if isinstance(t['ports'], str):
      t['ports'] = [t['ports']]
    elif not isinstance(t['ports'], list):
      t['ports'] = []
    t['ports'] = [s for s in (str(p).lstrip(':') for p in t['ports']) if s]

  # An untagged image resolves to ':latest', so make that explicit
  image = t.get('image')
  if isinstance(image, str) and image and ':' not in image.split('/')[-1]:
    t['image'] = f'{image}:latest'

  if isinstance(t.get('maintainer'), str):
    t['maintainer'] = t['maintainer'].strip()

  if 'logo' in t and not is_uri(t['logo']):
    del t['logo']

  if 'labels' in t:
    if not isinstance(t['labels'], list):
      t['labels'] = [t['labels']]
    t['labels'] = [{'name': str(l['name']), 'value': str(l['value'])}
                   for l in t['labels'] if isinstance(l, dict) and l.get('name') and l.get('value')]

  # Fix volume 'read_only' -> 'readonly' (and coerce to bool)
  if 'volumes' in t and not isinstance(t['volumes'], list):
    t['volumes'] = [t['volumes']]
  cleaned_volumes = []
  for vol in t.get('volumes', []):
    if not isinstance(vol, dict) or not vol.get('container'):
      continue
    if 'read_only' in vol:
      val = vol.pop('read_only')
      vol['readonly'] = val if isinstance(val, bool) else str(val).lower() == 'true'
    if 'readonly' in vol and not isinstance(vol['readonly'], bool):
      vol['readonly'] = str(vol['readonly']).lower() in ('true', '1')
    cleaned = {'container': str(vol['container'])}
    if 'bind' in vol:
      cleaned['bind'] = str(vol['bind'])
    if 'readonly' in vol:
      cleaned['readonly'] = vol['readonly']
    cleaned_volumes.append(cleaned)
  if 'volumes' in t:
    t['volumes'] = cleaned_volumes

  # Drop fields outside the schema ('_source'/'_priority' tags are kept for dedup, stripped later)
  for k in list(t):
    if k not in TEMPLATE_KEYS and not k.startswith('_'):
      del t[k]

def normalize_template_fields(templates):
  """Fix non-standard field names and malformed entries from upstream sources."""
  normalized = []
  for t in templates:
    try:
      normalize_template(t)
      normalized.append(t)
    except Exception as err:
      log.warning(f'Skipping unnormalizable template: {t.get("title", "<no title>")} ({err})')
  return normalized

def schema_errors(t):
  """Schema violations in a single template. Checked per template so that one bad
  entry from an upstream source gets dropped, rather than failing the whole build
  at the final validation."""
  probe = {k: v for k, v in t.items() if not k.startswith('_')}
  probe.setdefault('id', 1)  # combine assigns real ids at write time
  return [e.message for e in ITEM_VALIDATOR.iter_errors(probe)]

def is_valid_template(t):
  """Check a template has the required fields for its type."""
  if not (isinstance(t.get('title'), str) and t['title'].strip()):
    return False
  if not (isinstance(t.get('description'), str) and t['description'].strip()):
    return False
  tmpl_type = t.get('type', 1)
  if not isinstance(tmpl_type, int):
    return False
  if tmpl_type == 1 and 'image' not in t:
    return False
  if tmpl_type in (2, 3) and 'repository' not in t:
    return False
  if tmpl_type == 4 and 'repository' not in t and 'stackFile' not in t:
    return False
  return True

GITHUB_LINK = re.compile(r'\bgithub\.com/([\w.-]+)/([\w.-]+)', re.I)
# Owners and monorepos that republish other people's apps, so aren't the app itself
NOT_THE_APP = {'sponsors', 'orgs', 'apps', 'topics', 'about', 'features', 'marketplace',
               'linuxserver', 'pi-hosted/pi-hosted'}

def app_repo(t):
  """The app's own repo: the first github link in its text, else its GHCR namespace."""
  image = str(t.get('image') or '').split('@')[0].split(':')[0].split('/')
  match = GITHUB_LINK.search(f'{t.get("description") or ""} {t.get("note") or ""}')
  found = [match.groups()] if match else []
  if len(image) == 3 and image[0] == 'ghcr.io':
    found.append(tuple(image[1:]))
  for owner, repo in found:
    if repo and not {owner.lower(), f'{owner}/{repo}'.lower()} & NOT_THE_APP:
      return f'{owner}/{repo}'
  return None

def backfill_app_source(templates, candidates):
  """Re-attach the app's repo where dedup or the container/stack split lost it."""
  known = {}
  for t in sorted(candidates, key=lambda t: t.get('_priority', 0)):
    repo = app_repo(t)
    if repo and isinstance(t.get('title'), str):
      known.setdefault(normalize_string(t['title']), repo)
  filled = 0
  for t in templates:
    repo = known.get(normalize_string(t['title']))
    if repo and not app_repo(t):
      t['description'] = f'{t["description"].rstrip()}\n\nSource: https://github.com/{repo}'
      filled += 1
  return filled

def deduplicate_and_normalize(templates, overrides=None):
  """Filter invalid, deduplicate by (title, type) keeping the highest-ranked, and normalize category names."""
  overrides = overrides or {}
  best = {}
  for t in templates:
    if not is_valid_template(t):
      log.warning(f'Skipping invalid template: {t.get("title", "<no title>")}')
      continue
    problems = schema_errors(t)
    if problems:
      log.warning(f'Skipping template that fails the schema: {t["title"]} '
                  f'from {t["_source"]} ({problems[0]})')
      continue
    key = (normalize_string(t['title']), t.get('type', 1))
    pinned = pinned_source(overrides, key)
    if key not in best or template_rank(t, pinned) > template_rank(best[key], pinned):
      best[key] = t
  result = []
  for t in best.values():
    cats = {}
    for c in t.get('categories', []):
      label = normalize_category(c)
      if label:
        cats.setdefault(label.lower(), label)
    t['categories'] = list(cats.values())
    result.append(t)
  return result

def category_key(label):
  """Key that ignores spacing and a plural 's', so near-identical spellings collapse."""
  key = re.sub(r'[^a-z0-9]', '', label.lower())
  return key[:-1] if key.endswith('s') and len(key) > 4 else key

def canonical_categories(templates):
  """One spelling per category, so 'Database' and 'Databases' don't split the listings."""
  counts = Counter(c for t in templates for c in t.get('categories', []))
  best = {}
  for label in sorted(counts, key=lambda l: (-len(l.split()), -counts[l], l)):
    best.setdefault(category_key(label), label)
  for t in templates:
    if t.get('categories'):
      t['categories'] = list(dict.fromkeys(best[category_key(c)] for c in t['categories']))
  return len(counts) - len(best)

def postfix_ambiguous_titles(templates):
  """Append type labels to titles that appear with multiple types."""
  # Pass 1: postfix titles that share a normalized name across different types
  title_counts = Counter(normalize_string(t['title']) for t in templates)
  ambiguous = {title for title, count in title_counts.items() if count > 1}
  postfixed = set()
  for t in templates:
    if normalize_string(t['title']) in ambiguous:
      tmpl_type = t.get('type', 1)
      label = TYPE_LABELS.get(tmpl_type, f'type{tmpl_type}')
      t['title'] = f"{t['title']} ({label})"
      postfixed.add(id(t))

  # Pass 2: postfix any NEW collisions created by pass 1
  post_counts = Counter(normalize_string(t['title']) for t in templates)
  new_ambiguous = {title for title, count in post_counts.items() if count > 1}
  for t in templates:
    if normalize_string(t['title']) in new_ambiguous and id(t) not in postfixed:
      tmpl_type = t.get('type', 1)
      label = TYPE_LABELS.get(tmpl_type, f'type{tmpl_type}')
      t['title'] = f"{t['title']} ({label})"

def audit_overrides(templates, overrides):
  """Warn about overrides that stopped earning their place, so the file doesn't go stale."""
  groups = {}
  for t in templates:
    if is_valid_template(t):
      groups.setdefault((normalize_string(t['title']), t.get('type', 1)), []).append(t)
  for key, entry in sorted(overrides.items()):
    where = f'{entry["title"]} (type {key[1]})'
    candidates = groups.get(key)
    if not candidates:
      log.warning(f'Stale override: no template matches {where}')
      continue
    prefer = entry.get('prefer')
    if not prefer:
      continue
    pinned = f'{prefer}.json'
    if not any(t['_source'] == pinned for t in candidates):
      log.warning(f'Ineffective override: {prefer} has no copy of {where}, using normal priority')
    elif max(candidates, key=template_rank) is max(candidates, key=lambda t: template_rank(t, pinned)):
      log.warning(f'Redundant override: normal priority already picks {prefer} for {where}')

def audit_app_sources(templates, sources):
  """An author who publishes their own template, but whose app also still sits in
  sources/local/, never gets their updates published: local wins every time.
  Warn, so the stale local copy can be deleted."""
  local = {}
  for t in templates:
    if t['_priority'] == 0 and isinstance(t.get('title'), str):
      local.setdefault((normalize_string(t['title']), t.get('type', 1)), t['_source'])
  app_files = {s.filename for s in sources if s.is_app}
  for t in templates:
    if t['_source'] not in app_files or not isinstance(t.get('title'), str):
      continue
    shadowed_by = local.get((normalize_string(t['title']), t.get('type', 1)))
    if shadowed_by:
      log.warning(f'{t["title"]} from the {t["_source"]} app source is shadowed by the '
                  f'copy in sources/local/{shadowed_by}, so the author\'s updates never '
                  'get published. Delete the local copy to let them through.')

def missing_sources(sources, kind):
  """Sources of one kind that sources.csv lists but nothing was downloaded for."""
  external_dir = os.path.join(SOURCES_DIR, 'external')
  present = set(os.listdir(external_dir)) if os.path.isdir(external_dir) else set()
  return sorted(s.name for s in sources if s.kind == kind and s.filename not in present)

if __name__ == '__main__':
  banner('Combine', 'Merge, normalize + dedupe template sources into templates.json')
  overrides = load_overrides()
  sources = sources_list.load()
  raw = normalize_template_fields(load_sources(sources))
  sources_count = len({t.get('_source') for t in raw})
  log.info(f'Normalized {len(raw)} templates from {sources_count} sources')
  if any(s.is_app for s in sources):
    audit_app_sources(raw, sources)
  if overrides:
    log.info(f'Applying {len(overrides)} template overrides')
    audit_overrides(raw, overrides)
  templates = deduplicate_and_normalize(raw, overrides)
  log.info(f'{len(templates)} unique templates after dedup ({len(raw) - len(templates)} removed)')
  merged = canonical_categories(templates)
  if merged:
    log.info(f'Merged {merged} duplicate category spellings')
  filled = backfill_app_source(templates, raw)
  if filled:
    log.info(f'Restored the app source link on {filled} descriptions')
  postfix_ambiguous_titles(templates)
  # Strip internal tags
  for t in templates:
    t.pop('_source', None)
    t.pop('_priority', None)
  templates.sort(key=lambda t: t['title'].lower())
  for i, t in enumerate(templates, start=1):
    t['id'] = i
  out_path = os.path.join(BASE_DIR, 'templates.json')
  try:
    with open(out_path) as f:
      previous = len(json.load(f)['templates'])
  except (OSError, ValueError, KeyError):
    previous = 0
  # An app source going away costs only its own app, so it just gets left out
  unavailable = missing_sources(sources, sources_list.APP)
  if unavailable:
    log.warning(f'{len(unavailable)} app sources were not downloaded, so they are left '
                f'out of this build: {", ".join(unavailable)}')
  # A failed collection download must not silently shrink the published list
  missing = missing_sources(sources, sources_list.COLLECTION)
  if missing and not os.environ.get('ALLOW_SHRINK'):
    log.error(f'Refusing to write: missing external sources: {", ".join(missing)}. '
              'Set ALLOW_SHRINK=1 if this is intentional.')
    sys.exit(1)
  if len(templates) < previous * 0.9 and not os.environ.get('ALLOW_SHRINK'):
    log.error(f'Refusing to write: template count fell from {previous} to {len(templates)}. '
              'Set ALLOW_SHRINK=1 if this is intentional.')
    sys.exit(1)
  output = {'version': '3', 'templates': templates}
  try:
    jsonschema.Draft7Validator(SCHEMA, format_checker=FORMAT_CHECKER).validate(output)
  except jsonschema.ValidationError as ve:
    log.error(f'Refusing to write: output fails schema at {ve.json_path}: {ve.message}')
    sys.exit(1)
  log.info(f'Writing {len(templates)} templates to templates.json (previously {previous})')
  with open(out_path, 'w') as f:
    json.dump(output, f, indent=2, sort_keys=False)
