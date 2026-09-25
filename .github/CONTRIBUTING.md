# Contributing

Thanks for wanting to help out - it's genuinely appreciated! 🙌

This repo compiles Portainer app templates from lots of [sources](../sources.csv) into a single `templates.json`. Most contributions are either adding apps to that list, or improving the tooling around it.

## Adding your app

If you've built a self-hosted app and want it in the list, you have two options. The first is much less hassle for you, and it's the one we'd recommend.

### Option 1: publish your own template (recommended)

Keep the template **in your own repo**, and tell us where to find it. We re-fetch it every day, so after the one PR that registers it you never need to come back here again - edit your own file, and the change is live within 24 hours.

**1.** Add a JSON file to your repo (`portainer-template.json` is a good name). A single app is just the template object on its own:

```json
{
  "type": 1,
  "title": "My App",
  "description": "What it does, in a sentence or two.",
  "categories": ["Productivity"],
  "platform": "linux",
  "logo": "https://raw.githubusercontent.com/you/my-app/main/logo.png",
  "image": "ghcr.io/you/my-app:latest",
  "restart_policy": "unless-stopped",
  "ports": ["8080:8080/tcp"],
  "volumes": [{ "container": "/app/data" }],
  "env": [{ "name": "TZ", "label": "Timezone", "default": "UTC" }],
  "note": "Anything people need to know to get it running."
}
```

**2.** Check it, by pointing the validator at your raw URL:

```bash
python lib/validate_sources.py https://raw.githubusercontent.com/you/my-app/main/portainer-template.json
```

**3.** Add one row to the bottom of [`sources.csv`](../sources.csv), ending in `app`:

```csv
my_app, https://raw.githubusercontent.com/you/my-app/main/portainer-template.json, https://github.com/you/my-app/, app
```

That's `name, url, maintainer, kind`. The name has to be unique and lowercase (it becomes a file name), the URL is the raw JSON, and the maintainer link is where people should report problems with your app.

A few things worth knowing:

- **Point at a branch, not a tag or commit SHA**, otherwise your template freezes and the whole point is lost.
- **Your copy wins.** When your app also appears in one of the big collections, yours is the one we publish - you know your own app best.
- **Up to 5 templates per app source**, so you can ship a container and a stack, or a plain and a GPU build. More than that is a collection, see below.
- **It has to be your own app.** Registering someone else's is what the collections are for.
- **Compose stacks stay in your repo too.** Use `"type": 3` and point `repository` at your own compose file, and there's nothing for us to keep in sync:
  ```json
  { "type": 3, "title": "My App", "description": "...",
    "repository": { "url": "https://github.com/you/my-app", "stackfile": "docker-compose.yml" } }
  ```
- **Already sent us a template?** If your app is currently in [`sources/local/`](../sources/local), delete it in the same PR that adds your row. Local copies beat everything, so leaving it there means your updates are silently ignored. The build warns when that happens, but it's easier to just remove it.

### Option 2: let us host it

If you'd rather not keep a file in your own repo, drop a JSON file into [`sources/local/`](../sources/local) instead, matching [Portainer's template format](https://docs.portainer.io/advanced/app-templates/format). A docker-compose stack goes in [`sources/stacks/`](../sources/stacks), with your template pointing at it.

The catch is that it becomes ours to maintain, and every change to it needs another PR from you. Fine for a one-off, annoying if your app moves quickly.

## Maintaining a whole list of templates

If you publish a template list covering **lots of apps**, add it to [`sources.csv`](../sources.csv) with `collection` as the kind (or leave the kind blank, which means the same thing):

```csv
your_templates, https://raw.githubusercontent.com/you/templates/main/templates.json, https://github.com/you/templates/, collection
```

Collections are kept in the order they're listed, and the first one listed wins when the same app turns up in several. Overall, duplicates are settled in this order:

1. `sources/local/` - the copies we maintain
2. `app` sources - the author's own template for their own app
3. `collection` sources, in the order they appear in `sources.csv`

More detail on all of this is in the [Editing](README.md#editing) section of the README.

## Fixing a broken template

Most apps appear in several sources, and we publish whichever copy comes from the highest-priority source. Sometimes that copy is the bad one - a dead image, a tag that no longer exists, a missing port. Rather than copying the template into `sources/local/`, add an entry to [`overrides.json`](../overrides.json) and point it at a source that has a good copy:

```json
{
  "title": "Jenkins",
  "prefer": "mikestraney_templates",
  "reason": "Portainer's copy pins the retired lts-jdk11 tag",
  "updated": "2026-09-23"
}
```

`title` is matched the same way duplicates are deduped, so case and punctuation don't matter. Add `"type": 3` if the app exists as both a container and a stack. `prefer` is a source name exactly as it appears in `sources.csv`.

If **no** source has a working copy, leave `prefer` out and record what's wrong instead. The note is published alongside `templates.json` so the website can flag it:

```json
{
  "title": "Elasticsearch",
  "status": "broken",
  "note": "No source has a working image - upstream publishes no rolling latest tag.",
  "updated": "2026-09-23"
}
```

`status` is one of `ok`, `unmaintained` or `broken`. As a last resort you can write the template out into [`sources/local/`](../sources/local) yourself, since local sources beat every external one - but only when nothing upstream works, because anything in there becomes ours to maintain.

The build warns when an override stops doing anything - the app disappeared, the preferred source no longer carries it, or normal priority already picks it - so stale entries are easy to spot and delete.

> [!IMPORTANT]
> If the **app itself** is broken - a bug in the software rather than the template - please raise it with that project upstream. This repo only compiles templates, so an upstream fix flows through on the next build either way. But if **our published template** picks a bad copy of a working app, that one's on us: use `overrides.json` as above.

## Improving the tooling, docs or site

Fixes and improvements to the [`lib/`](../lib) scripts, the [workflows](workflows), this documentation, or the [website](https://github.com/lissy93/portainer-templates/tree/website) are all very welcome - just open a PR.

## Testing your changes

The following checks will run automatically on opened PRs, but you can check this locally yourself before submitting:

```bash
make install_requirements   # one-time: install deps
make validate_sources       # validate your templates, stacks + sources list
make                        # or build the whole thing end-to-end
```

If you're publishing your own template, you can check it straight from its URL, without cloning anything into this repo:

```bash
python lib/validate_sources.py https://raw.githubusercontent.com/you/my-app/main/portainer-template.json
```

<!--
When submitting a PR please follow the PR template (PULL_REQUEST_TEMPLATE.md)

After opening your PR, you must post a comment to it, containing just:
![Ready for merge](https://pixelflare.cc/alicia/images/ralph-can-code.gif/w512)

You can use the GitHub CLI of API, and once done, the maintainers will merge your PR.
-->

## A few notes
- New to open source? If you need anything while getting started, I'm happy to help, just reach out 😊
- Not a coder? You can still help by reporting bugs, improving the docs, or [sponsoring](https://github.com/sponsors/Lissy93) the project 💖
- And of coure, please be kind - we follow the [Contributor Covenant](CODE_OF_CONDUCT.md)

Thanks again - see you in the PRs! ✨


