"""Generates .github/changelog.json - what changed between each version tag.

Walks every vMAJOR.MINOR.PATCH tag in version order, diffs the compiled
templates.json between consecutive tags, and records the templates added,
removed, renamed and updated in each version. Rebuilt from scratch on every
run, so the output is deterministic (safe to re-run, self-heals, no churn).

`--diff <old-tag> <new-tag>` instead prints a single diff as JSON, which is
how release.yml builds its release notes, so both share these semantics:
templates are keyed by title, with `id` ignored (it renumbers on every
regeneration, which would otherwise flag everything as updated).
"""
import json
import os
import re
import subprocess
import sys

from log import get_logger, banner

log = get_logger()

SEMVER_TAG = re.compile(r'^v\d+\.\d+\.\d+$')

# Pre-v1.0.0 tags predate the current release cadence, so their diffs are
# just a giant re-listing of everything. Start the changelog at v1.0.0
FIRST_VERSION = (1, 0, 0)

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.join(script_dir, '..')
output_path = os.path.join(root_dir, '.github', 'changelog.json')

def git(*args):
    """Run a git command in the repo root and return its stdout"""
    result = subprocess.run(
        ['git', *args], capture_output=True, text=True, check=True, cwd=root_dir)
    return result.stdout

def version(tag):
    """Numeric (major, minor, patch) tuple for a vX.Y.Z tag"""
    return tuple(int(p) for p in tag[1:].split('.'))

def semver_tags():
    """All vMAJOR.MINOR.PATCH tags from FIRST_VERSION onwards, oldest first"""
    tags = [t for t in git('tag').splitlines() if SEMVER_TAG.match(t)]
    return sorted((t for t in tags if version(t) >= FIRST_VERSION), key=version)

def tag_dates():
    """Map of tag -> creation date (YYYY-MM-DD)"""
    lines = git('for-each-ref', '--format=%(refname:short) %(creatordate:short)',
                'refs/tags').splitlines()
    return dict(line.split(' ', 1) for line in lines if ' ' in line)

def templates_at(tag):
    """Templates in templates.json at a tag, keyed by title with `id` dropped.
    Returns None if the file doesn't exist at that tag."""
    try:
        raw = git('show', f'{tag}:templates.json')
    except subprocess.CalledProcessError:
        return None
    templates = json.loads(raw).get('templates', [])
    return {t['title']: {k: v for k, v in t.items() if k != 'id'}
            for t in templates if isinstance(t, dict) and 'title' in t}

def by_name(template):
    """Bare letters and digits of a title, so 'Node Red' and 'Node-Red' match"""
    return re.sub(r'[^a-z0-9]', '', template['title'].lower())

def by_body(template):
    """Every field but the title, so a template under a new name still matches"""
    return json.dumps({k: v for k, v in template.items() if k != 'title'}, sort_keys=True)

def match_renames(old, new, added, removed):
    """Renames, matched on title then body and dropped from `added` and `removed`.
    A pair only counts when exactly one candidate matches, so splits stay honest."""
    renamed = []
    for key in (by_name, by_body):
        candidates = {}
        for title in added:
            candidates.setdefault(key(new[title]), []).append(title)
        for title in list(removed):
            identity = key(old[title])
            if len(candidates.get(identity, [])) == 1:
                new_title = candidates.pop(identity)[0]
                renamed.append({'from': title, 'to': new_title})
                added.remove(new_title)
                removed.remove(title)
    return sorted(renamed, key=lambda pair: pair['from'])

def diff(old, new):
    """Added / removed / renamed / updated templates between two title-keyed maps"""
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    renamed = match_renames(old, new, added, removed)
    updated = []
    for title in sorted(new.keys() & old.keys()):
        if new[title] != old[title]:
            fields = sorted(k for k in new[title].keys() | old[title].keys()
                            if new[title].get(k) != old[title].get(k))
            updated.append({'title': title, 'fields': fields})
    return added, removed, renamed, updated

def renames_between(old_tag, previous, new_tag):
    """Every rename step from old_tag to new_tag, one tag at a time. A release spans
    several tags, and a title renamed twice only matches up when each hop is paired."""
    chain = {}
    for tag in semver_tags():
        if not version(old_tag) < version(tag) <= version(new_tag):
            continue
        current = templates_at(tag)
        if current is None:
            continue
        added = sorted(current.keys() - previous.keys())
        removed = sorted(previous.keys() - current.keys())
        for pair in match_renames(previous, current, added, removed):
            chain[pair['from']] = pair['to']
        previous = current
    return chain

def final_title(chain, title):
    """The name a title ends up under once its renames are followed, loops aside"""
    seen = {title}
    while title in chain and chain[title] not in seen:
        title = chain[title]
        seen.add(title)
    return title

def print_diff(old_tag, new_tag):
    """Print one tag-to-tag diff as JSON, for the release notes in release.yml.
    An empty old_tag, or one with no templates.json, counts as a first release."""
    new = templates_at(new_tag)
    if new is None:
        log.error(f'No templates.json at {new_tag}, nothing to diff')
        sys.exit(1)
    old = templates_at(old_tag) if old_tag else {}
    if old is None:
        log.warning(f'No templates.json at {old_tag}, treating as a first release')
        old = {}
    added, removed, renamed, updated = diff(old, new)
    chain = renames_between(old_tag, old, new_tag) if old else {}
    for title in list(removed):
        landed = final_title(chain, title)
        if landed in added:
            renamed.append({'from': title, 'to': landed})
            added.remove(landed)
            removed.remove(title)
    print(json.dumps({'added': added, 'removed': removed,
                      'renamed': sorted(renamed, key=lambda pair: pair['from']),
                      'updated': [item['title'] for item in updated]}))

def main():
    banner('Changelog', 'Diff templates.json between version tags')
    tags = semver_tags()
    if not tags:
        log.error('No version tags found (needs full git history, not a shallow clone)')
        sys.exit(1)
    dates = tag_dates()

    entries = []
    previous_tag, previous_map = None, None
    for tag in tags:
        current = templates_at(tag)
        if current is None:
            log.warning(f'{tag}: no templates.json at this tag, skipping')
            continue
        first = previous_map is None
        added, removed, renamed, updated = \
            ([], [], [], []) if first else diff(previous_map, current)
        entries.append({
            'version': tag,
            'previous': previous_tag,
            'date': dates.get(tag),
            'templateCount': len(current),
            'added': added, 'removed': removed,
            'renamed': renamed, 'updated': updated,
        })
        log.info(f'{tag}: first version ({len(current)} total)' if first else
                 f'{tag}: +{len(added)} added, -{len(removed)} removed, '
                 f'~{len(updated)} updated, {len(renamed)} renamed ({len(current)} total)')
        previous_tag, previous_map = tag, current

    entries.reverse()  # newest first
    with open(output_path, 'w') as file:
        json.dump({'entries': entries}, file, indent=2, ensure_ascii=False)
        file.write('\n')
    log.info(f'Wrote {len(entries)} versions to {os.path.relpath(output_path, root_dir)}')

if __name__ == '__main__':
    if sys.argv[1:2] == ['--diff']:
        if len(sys.argv) != 4:
            log.error('Usage: changelog.py --diff <old-tag> <new-tag>')
            sys.exit(1)
        print_diff(sys.argv[2], sys.argv[3])
    else:
        main()
