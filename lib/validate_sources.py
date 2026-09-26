import copy
import json
import os
import sys
from collections import Counter

from jsonschema import Draft7Validator, FormatChecker

import sources_list
from combine import normalize_template, normalize_string
from log import get_logger, banner

log = get_logger()

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
with open(os.path.join(ROOT, 'Schema.json')) as f:
    ITEM_SCHEMA = json.load(f)['properties']['templates']['items']
VALIDATOR = Draft7Validator(ITEM_SCHEMA, format_checker=FormatChecker())

# Absence of these only warrants a warning under the Moderate quality gate
RECOMMENDED = ['logo', 'categories', 'note', 'platform', 'restart_policy']
SOURCE_EXTENSIONS = ('.json', '.yml', '.yaml', '.csv')
OVERRIDE_STATUSES = ('ok', 'unmaintained', 'broken')

def load_json(path):
    with open(path) as file:
        return json.load(file)

def stackfile_errors(where, template):
    """A repo-relative stackfile (sources/…) must exist on disk or the stack can't deploy"""
    stackfile = (template.get('repository') or {}).get('stackfile')
    if isinstance(stackfile, str) and stackfile.startswith('sources/') \
       and not os.path.exists(os.path.join(ROOT, stackfile)):
        return [f'{where}: stackfile not found in repo: {stackfile}']
    return []

def quality_warnings(where, template):
    """Non-blocking completeness checks: recommended fields present, no duplicate env vars"""
    warnings = [f'{where}: missing recommended field "{key}"'
                for key in RECOMMENDED if not template.get(key)]
    names = [e.get('name') for e in template.get('env', []) if isinstance(e, dict)]
    warnings += [f'{where}: duplicate env variable {name!r}'
                 for name, count in Counter(names).items() if count > 1]
    return warnings

def check_templates(name, templates):
    """Validate a list of templates: each must normalize to a schema-valid entry"""
    errors, warnings, seen = [], [], Counter()
    if not templates:
        warnings.append(f'{name}: contains no templates')
    for index, raw in enumerate(templates):
        where = f'{name}#{index}'
        if not isinstance(raw, dict):
            errors.append(f'{where}: template is not an object')
            continue
        title = raw.get('title')
        if isinstance(title, str) and title.strip():
            where = f'{name} "{title.strip()}"'
        # Normalize a copy exactly as combine.py would, then judge the published result
        try:
            template = copy.deepcopy(raw)
            normalize_template(template)
        except Exception as err:
            errors.append(f'{where}: failed to normalize ({err})')
            continue
        template.setdefault('id', 1)  # combine assigns real ids at write time
        errors += [f'{where}: {e.message}' for e in VALIDATOR.iter_errors(template)]
        errors += stackfile_errors(where, template)
        warnings += quality_warnings(where, template)
        # Keyed like combine's dedup, so one app may ship both a container and a stack
        if isinstance(title, str) and title.strip():
            seen[(normalize_string(title), template.get('type', 1))] += 1

    errors += [f'{name}: duplicate template {title!r} of type {kind} ({count} copies), '
               'so all but one would be dropped'
               for (title, kind), count in seen.items() if count > 1]
    return errors, warnings

def check_source(path):
    """Validate one local source: each template must normalize to a schema-valid entry"""
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as err:
        return [f'{path}: could not read JSON ({err})'], []

    templates = data.get('templates') if isinstance(data, dict) else None
    if not isinstance(templates, list):
        return [f'{path}: missing top-level "templates" array'], []

    return check_templates(os.path.basename(path), templates)

def url_kind(url):
    """The kind sources.csv gives this url, defaulting to app, the stricter of the two"""
    for source in sources_list.load():
        if source.url == url:
            return source.kind
    return sources_list.APP

def check_url(url):
    """Check a remote source as download.py will: an app per template, a collection on shape"""
    payload, error = sources_list.fetch_json(url)
    if error:
        return [f'{url}: could not fetch valid JSON ({error})'], []

    is_app = url_kind(url) == sources_list.APP
    templates = sources_list.templates_in(payload, allow_bare=is_app)
    if not templates:
        shape = ('one template as a JSON object, or several under a top-level "templates" '
                 'array') if is_app else 'its templates under a top-level "templates" array'
        return [f'{url}: found no templates, so the build would skip it. Publish {shape}'], []
    if not is_app:
        return [], []  # the shape is all the build needs from a collection
    if len(templates) > sources_list.MAX_APP_TEMPLATES:
        return [f'{url}: holds {len(templates)} templates, but an app source may publish only '
                f'{sources_list.MAX_APP_TEMPLATES}, so it has to be listed as a collection'], []
    return check_templates(url, templates)

def check_stack(path):
    """A compose stack file must be valid YAML with a non-empty services mapping"""
    try:
        import yaml
    except ImportError:
        return [], [f'{path}: PyYAML not installed, skipped stack validation']
    try:
        with open(path) as file:
            doc = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as err:
        return [f'{path}: invalid YAML ({err})'], []
    if not isinstance(doc, dict) or not isinstance(doc.get('services'), dict) or not doc['services']:
        return [f'{path}: compose file has no services'], []
    warnings = [f'{path}: obsolete top-level "version" key'] if 'version' in doc else []
    return [], warnings

def local_source_names():
    """File names in sources/local/, which a source name can't reuse: combine keys on them"""
    local_dir = os.path.join(ROOT, 'sources', 'local')
    if not os.path.isdir(local_dir):
        return set()
    return {os.path.splitext(f)[0] for f in os.listdir(local_dir) if f.endswith('.json')}

def check_csv(path):
    """Validate sources.csv: unique file-name-safe name, http(s) url, recognized kind"""
    try:
        rows = list(sources_list.rows(path))
    except OSError as err:
        return [f'{path}: could not read ({err})'], []

    local_names = local_source_names()
    errors, warnings, seen = [], [], set()
    for line, cells in rows:
        for level, message in sources_list.row_problems(cells):
            (errors if level == 'error' else warnings).append(f'{path}:{line}: {message}')
        name = cells[0]
        if not name:
            continue
        if name in seen:
            errors.append(f'{path}:{line}: duplicate source name {name!r}')
        seen.add(name)
        if name in local_names:
            errors.append(f'{path}:{line}: name {name!r} collides with '
                          f'sources/local/{name}.json, which would leave the two '
                          'indistinguishable when picking between duplicate templates')
    return errors, warnings

def source_names():
    """Source names from sources.csv, used to check an override's "prefer" target exists"""
    try:
        return {source.name for source in sources_list.load()}
    except OSError:
        return set()

def check_overrides(path):
    """Validate overrides.json: each entry must name a real template and actually do something"""
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as err:
        return [f'{path}: could not read JSON ({err})'], []

    entries = data.get('overrides') if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [f'{path}: missing top-level "overrides" array'], []

    name = os.path.basename(path)
    known = source_names()
    errors, warnings, keys = [], [], Counter()
    for index, entry in enumerate(entries):
        where = f'{name}#{index}'
        if not isinstance(entry, dict):
            errors.append(f'{where}: override is not an object')
            continue
        title = entry.get('title')
        if not (isinstance(title, str) and title.strip()):
            errors.append(f'{where}: missing "title"')
            continue
        where = f'{name} "{title.strip()}"'
        template_type = entry.get('type', 1)
        if not isinstance(template_type, int) or template_type not in (1, 2, 3, 4):
            errors.append(f'{where}: "type" must be 1, 2, 3 or 4')
            continue
        keys[(normalize_string(title), template_type)] += 1
        prefer = entry.get('prefer')
        if prefer is not None and prefer not in known:
            errors.append(f'{where}: "prefer" names an unknown source {prefer!r}')
        status = entry.get('status')
        if status is not None and status not in OVERRIDE_STATUSES:
            errors.append(f'{where}: "status" must be one of {", ".join(OVERRIDE_STATUSES)}')
        if not any(entry.get(key) for key in ('prefer', 'status', 'note')):
            warnings.append(f'{where}: has no "prefer", "status" or "note", so it does nothing')

    errors += [f'{name}: duplicate override for {title!r} (type {kind}, {count} entries)'
               for (title, kind), count in keys.items() if count > 1]
    return errors, warnings

def is_url(target):
    return target.startswith(('http://', 'https://'))

def validate_path(path):
    """Dispatch a target to the right checker, by url then file name then extension"""
    if is_url(path):
        return check_url(path)
    if os.path.basename(path) == 'overrides.json':
        return check_overrides(path)
    if path.endswith('.csv'):
        return check_csv(path)
    if path.endswith(('.yml', '.yaml')):
        return check_stack(path)
    return check_source(path)

def expand(targets):
    """Turn any directory argument into the source files it contains (recursively)"""
    paths = []
    for target in targets:
        if is_url(target):
            paths.append(target)
        elif os.path.isdir(target):
            for root, _, names in os.walk(target):
                paths += [os.path.join(root, n) for n in names if n.endswith(SOURCE_EXTENSIONS)]
        else:
            paths.append(target)
    return sorted(paths)

def main():
    banner('Validate sources', 'Check template sources, stack files, sources.csv + overrides')
    targets = sys.argv[1:] or [os.path.join(ROOT, p) for p in
                               ('sources/local', 'sources/stacks', 'sources.csv', 'overrides.json')]

    errors, warnings = [], []
    for path in expand(targets):
        if not is_url(path) and not os.path.exists(path):
            log.warning(f'Skipping missing path: {path}')
            continue
        file_errors, file_warnings = validate_path(path)
        errors += file_errors
        warnings += file_warnings
        where = path if is_url(path) else os.path.relpath(path, ROOT)
        log.info(f'Checked {where}: '
                 f'{len(file_errors)} errors, {len(file_warnings)} warnings')

    for warning in warnings:
        log.warning(warning)
    for error in errors:
        log.error(error)
    if errors:
        log.error(f'Source validation failed ({len(errors)} errors)')
        sys.exit(1)
    log.info(f'All sources valid ({len(warnings)} warnings)')

if __name__ == '__main__':
    main()
