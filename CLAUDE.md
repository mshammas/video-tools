# Working in this repo

## Check the public landing page

These tools are documented for end users at <https://tools.shammas.in/yt-download/>.
That page is **not** in this repo — its source is `yt-download/index.html` in the
`tools` repo (locally `~/github/tools/`), with its own commits and its own deploy.

Before wrapping up any change a user would notice, ask Shammas whether the page
needs updating too. Usually it does, but ask rather than assume, and never edit
another repo without saying so first.

Changes that almost always reach the page:

- a new release — the page names a version and links its installers
- anything in "Before you start" or "Installing", which spell out the per-OS
  requirements (this is where the Windows advice lives)
- a new, renamed or removed option, or a reworked tab
- anything that changes what a tab needs installed to work

Changes that usually do not: refactors, packaging internals, test or CI work.
