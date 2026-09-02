# agentoffice

![agentoffice](docs/cover.png)

A local dashboard that analyses your projects and suggests the next steps. No server,
no dependencies — python3 stdlib plus `claude -p` as the LLM runtime (it inherits your
login and model from `~/.claude/settings.json`, so no API key is needed).

```bash
python3 office.py serve              # http://localhost:8765
python3 office.py run [name] [lens]  # lens: all|code|market|money (default all)
python3 office.py answer <name> "…"
python3 office.py add <name> <path>     # or the form in the sidebar
python3 office.py test               # assert-based self-check
```

## Files

| File | What |
|---|---|
| `office.py` | CLI, http server, context gathering, the agent loop |
| `dashboard.html` | the whole UI, vanilla JS, no build step |
| `projects.json` | which projects are tracked (local, git-ignored) |
| `state/<name>.json` | the conversation per project (local, git-ignored) |

## Getting started

Requires Python 3.8+ and the `claude` CLI logged in. Create `projects.json`:

```json
{"projects": [
  {"name": "myproject", "path": "~/projects/myproject", "tracked": true,
   "status": "short status line", "extra": ["~/documents/analysis.md"]}
]}
```

`extra` takes a file, a glob, or a directory (a directory reads `*.md` + `*.json` one
level down). It is only read by the market/money/all lenses. `status` goes at the top
of the context.

## Lenses

| Lens | Role | Sources |
|---|---|---|
| `code` | tech lead | CLAUDE.md, README, LEFT_TO_DO, CHANGELOG, docs/*.md + git log + file tree |
| `market` | operator/CMO | STRATEGY/, PRICING*, *ROADMAP*, OWNER_ANALYSIS, *IDEAS* + `extra` |
| `money` | CFO | budget.json, finance/*.json, PRICING* + `extra` |
| `all` | weighs all three | all of the above |

## The flow

`POST /api/run {name, lens}` → `gather()` stitches together the project's files and git
log → `claude -p` → the response JSON (`status: "question"` or `"plan"`) is appended to
`state/<name>.json`. The officer sees the full history on the next run, so completed
actions and your comments shape the next plan.

## Note on language

The UI and the officer's responses are in Swedish (see the prompt in `office.py`).

## License

MIT
