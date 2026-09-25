import json
import html
import urllib.parse
import os
import re
import sys

import sources_list
from log import get_logger, banner

log = get_logger()

current_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(current_dir)
readme_path = os.path.join(project_dir, '.github/README.md')
templates_path = os.path.join(project_dir, 'templates.json')
external_dir = os.path.join(project_dir, 'sources/external')

def load_json_file(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

def slugify(title: str):
    baseUrl = 'https://portainer-templates.as93.net'
    slug = re.sub(r'\s+', '-', re.sub(r'[^a-z0-9 ]', '', title.lower()).strip())
    return f'{baseUrl}/{slug}'

def clean_text(text):
    """Remove quotes/brackets and duplicate whitespace for html/md render"""
    text = (text or '').replace('"', '”').replace("'", '’').replace('<', '').replace('>', '')
    return re.sub(r'\s+', ' ', text).strip()

def generate_app_list():
  templates = load_json_file(templates_path)['templates']
  templates.sort(key=lambda template: template['title'].lower())
  markdown_content = ''
  for index, template in enumerate(templates):
      name = template['title']
      maintainer = template.get('maintainer')
      maintainer_md_link = f" -- ([Report issues]({maintainer}))" if maintainer else ''
      description = clean_text(template['description'])
      if 'logo' in template and template['logo']:
          logo = f"<img title=\"{description}\" src=\"{html.escape(template['logo'])}\" width='26' height='26' /> "
      else:
          logo = ' '
      markdown_content += f"{index+1}. {logo}**[{name}]({slugify(name)} '{description}')** {maintainer_md_link}\n"
  return markdown_content

def github_user(*urls):
    """The GitHub account behind a source, for its avatar and credit link. Falls back to
    the maintainer link, since a source's JSON may be served from anywhere."""
    for url in urls:
        parsed = urllib.parse.urlparse(url or '')
        path_parts = [p for p in parsed.path.split('/') if p]
        if parsed.hostname in ('github.com', 'raw.githubusercontent.com') and path_parts:
            return path_parts[0]
    return None

def source_credit(source, label):
    """One line of a README credit list: avatar, link and the author's handle"""
    username = github_user(source.url, source.maintainer)
    if not username:
        return f'[{label}]({source.url})'
    avatar = f'<img src="https://github.com/{username}.png?size=40" width="26" height="26" />'
    return (f'{avatar} [{label}]({source.url}) by '
            f'[@{username}](https://github.com/{username})')

def app_label(source):
    """An app source's own title, read back from what was downloaded, so the credit reads
    "Cantinarr" rather than "cantinarr". Falls back to the source name when unfetched."""
    try:
        templates = load_json_file(os.path.join(external_dir, source.filename))['templates']
        titles = [t['title'] for t in templates if isinstance(t, dict) and t.get('title')]
    except (OSError, ValueError, KeyError):
        titles = []
    return titles[0] if titles else source.name

def generate_sources_list(sources):
    """The collections: other people's maintained lists of templates"""
    return ''.join(f'{index}. {source_credit(source, "template")}\n'
                   for index, source in enumerate(sources, start=1))

def generate_app_sources_list(sources):
    """The individual apps, each published and kept up to date by its own author"""
    if not sources:
        return ('_None yet - if you maintain a self-hosted app, '
                '[add yours](CONTRIBUTING.md#option-1-publish-your-own-template-recommended)._')
    return ''.join(f'{index}. {source_credit(source, app_label(source))}\n'
                   for index, source in enumerate(sources, start=1))

def insert_content_between_markers(file_path, start_marker, end_marker, content_to_insert):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    start_index = -1
    end_index = -1

    for i, line in enumerate(lines):
        if start_marker in line:
            start_index = i
        if end_marker in line:
            end_index = i
            break

    if start_index < 0 or end_index <= start_index:
        log.error(f'Markers {start_marker} / {end_marker} not found in {file_path}')
        sys.exit(1)

    lines[start_index + 1:end_index] = [content_to_insert + '\n']

    with open(file_path, 'w') as file:
        file.writelines(lines)

banner('List', 'Render app + source lists into the README')

all_sources = sources_list.load()

# Insert the collection sources list into readme
sources_md = generate_sources_list([s for s in all_sources if not s.is_app])
insert_content_between_markers(
  readme_path,
  '<!-- auto-insert-sources:start -->',
  '<!-- auto-insert-sources:end -->',
  sources_md,
)
log.info(f'Rendered {sources_md.count(chr(10))} sources into README')

# Insert the individual app sources list into readme
app_sources = [s for s in all_sources if s.is_app]
insert_content_between_markers(
  readme_path,
  '<!-- auto-insert-app-sources:start -->',
  '<!-- auto-insert-app-sources:end -->',
  generate_app_sources_list(app_sources),
)
log.info(f'Rendered {len(app_sources)} individual app sources into README')

# Insert app list into readme
apps_md = generate_app_list()
insert_content_between_markers(
  readme_path,
  '<!-- auto-insert-apps:start -->',
  '<!-- auto-insert-apps:end -->',
  apps_md,
)
log.info(f'Rendered {apps_md.count(chr(10))} apps into README')
