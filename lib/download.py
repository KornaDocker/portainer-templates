import os
import sys
import json

import sources_list
from log import get_logger, banner

log = get_logger()

dir = os.path.dirname(os.path.abspath(__file__))

destination_dir = os.path.join(dir, '../sources/external')

# Downloads the templates file for a given source, to the local destination
def download(source):
    log.info(f'Downloading {source.url}')
    payload, error = sources_list.fetch_json(source.url)
    if error:
        log.warning(f'Skipping source due to an error: {source.url} ({error})')
        return False

    # An app source may publish a bare template object, since it's just the one
    templates = sources_list.templates_in(payload, allow_bare=source.is_app)
    if not templates:
        log.warning(f'Skipping source with no templates: {source.url}')
        return False

    # 'app' means one author's own app, not a back door for an unreviewed list
    if source.is_app and len(templates) > sources_list.MAX_APP_TEMPLATES:
        log.warning(f'Skipping {source.name}: an app source may hold at most '
                    f'{sources_list.MAX_APP_TEMPLATES} templates, but this one has '
                    f'{len(templates)}, so it belongs in sources.csv as a collection')
        return False

    # Add maintainer field to each template
    for t in templates:
        if isinstance(t, dict) and source.maintainer:
            t['maintainer'] = source.maintainer

    file_path = os.path.join(destination_dir, source.filename)
    log.debug(f'Saving to {os.path.abspath(file_path)}')
    with open(file_path, 'w') as f:
        json.dump({'templates': templates}, f, indent=2, sort_keys=False)
    return True

# Create destination folder if not yet present
if not os.path.exists(destination_dir):
  os.makedirs(destination_dir)

banner('Download', 'Fetch template sources listed in sources.csv')

sources = sources_list.load()
failures = [source for source in sources if not download(source)]

log.info(f'Downloaded {len(sources) - len(failures)}/{len(sources)} sources')

# A lost collection stops the build; a lost app source only costs its own app
failed_apps = [source.name for source in failures if source.is_app]
failed_collections = [source.name for source in failures if not source.is_app]
if failed_apps:
  log.warning(f'Leaving out {len(failed_apps)} unavailable app '
              f'sources: {", ".join(failed_apps)}')
if failed_collections:
  log.error(f'Failed to download valid sources: {", ".join(failed_collections)}')
  sys.exit(1)
