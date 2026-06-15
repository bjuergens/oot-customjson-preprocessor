
# OTT CustomJson Preprocessor

this is a python script, that reads video files such as mkv or mpv, and generates custom json file, that can be played with opentogethertube. 

this includes among other things:
* reencoding video tracks to be playable with browser
* extracting subtiles
* generating thumbnails
* estimating defaults


## Tooling

Managed with `uv`. The whole tool is a single module:
`src/ott_customjson_preprocessor/preprocessor.py`. Common commands:

- `uv sync` — create/refresh the dev environment
- `uv run pytest` — run tests
- `uv run ruff check .` — lint
- `uv run ruff format .` — format
- `uvx oot-customjson-preprocessor` — run the published tool
- `uv run src/ott_customjson_preprocessor/preprocessor.py` — run from the checkout

# General 

This section is the same for multiple projects. 

## Principles

- 📏 Big functions are fine. Extract when there's reuse or the established abstractions call for it.
- ⏳ No premature performance optimization.
- 📋 Plans define what and done when, not how. Challenge a plan when it fights reality; don't silently deviate.
- 🔊 Fail loudly. Throw errors, don't swallow them. Log failures clearly. If something is wrong, the developer should know immediately, not discover it later through subtle misbehavior.

## Emoji

Use consistently in code, commits, and logging.

### Commits

Human-made commits usually contain no emoji, while agent-made commits do.

`<emoji> <type>: <description>`

- ✨ feat: new feature
- 🐛 fix: bug fix
- 🔧 config: configuration changes
- 📦 deps: dependency changes
- 🧪 test: tests
- 📝 docs: documentation
- 🧹 refactor: cleanup (no behavior change)


### Logging

- ✅ success operations
- ❌ errors and failures
- ⚠️ warnings
