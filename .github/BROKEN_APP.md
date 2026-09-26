# Is an app or container not working?

Sorry it's playing up! But please read this before opening an issue here - it'll get you a fix far quicker...

**This repo doesn't build or maintain the apps themselves.** It only compiles Portainer templates from [many community sources](https://github.com/lissy93/portainer-templates#sources) into a single file. So when an app won't start, crashes, or misbehaves, the fix lives upstream - not here.

### Where to report it

- **A bug in the app itself** - report it to that **app's own project**.
- **A wrong template** (dead image, bad port, missing variable) - report it to the **source it came from**. Every app in the [README](https://github.com/lissy93/portainer-templates#supported-apps-and-stacks) has a **"Report issues"** link beside it, pointing straight at its source. For the growing number of apps whose own authors publish their template, that link goes directly to them, and their fix reaches us the next day.

Once a fix lands upstream, it flows into our list automatically on the next build.

### When to open an issue here

Please do, if the problem is with **this project** - the build tooling, the website, the docs, or a template we maintain ourselves in [`sources/local/`](https://github.com/lissy93/portainer-templates/tree/main/sources/local). 👍
