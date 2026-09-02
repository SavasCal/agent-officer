#!/usr/bin/env python3
"""agentoffice: track projects, ask the agent officer for next actions.
  python3 office.py run [name] [lens]   lens: all|code|market|money (default all)
  python3 office.py answer <name> "..."  answer the officer's questions
  python3 office.py add <name> <path>   track a new project
  python3 office.py serve [port]        dashboard at http://localhost:8765
  python3 office.py test                self-check
"""
import glob, json, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "projects.json")
STATE = os.path.join(HERE, "state")
SOURCES = {
    "code":   ["CLAUDE.md", "README.md", "LEFT_TO_DO.md", "CHANGELOG.md", "PLAN.md", "docs/*.md"],
    "market": ["README.md", "STRATEGY/*.md", "docs/PRICING*.md", "docs/*ROADMAP*.md", "docs/*ROAD_MAP*.md",
               "docs/SELF_SERVING*.md", "docs/OWNER_ANALYSIS.md", "docs/*IDEAS*.md", "docs/FEATURE_ANALYSIS*.md"],
    "money":  ["budget.json", "finance/*.json", "../finance/*.json", "docs/PRICING*.md", "STRATEGY/PRICING.md"],
}
LENSES = list(SOURCES) + ["all"]
ROLES = {
    "code":   "Du är teknisk lead. Fokusera på kodhälsa, drift, risk och vad som blockerar release.",
    "market": "Du är operatör/CMO. Prioritera tillväxt, positionering, pris, kanaler och kunder. Nämn kod bara när den blockerar affären.",
    "money":  "Du är CFO. Marginal, kalkyl, kassaflöde, prissättning och kostnadsdrivare — räkna på det du ser.",
    "all":    "Väg kod, marknad och ekonomi mot varandra och välj det som flyttar projektet mest just nu.",
}
PER_FILE, TOTAL = 12000, 70000
SKIP = {"node_modules", ".next", ".git", "backups", "dist", "build"}

PROMPT = """You are the agent officer: a sharp operator reviewing one of the user's projects to decide the single next move.
Below is the project's documentation, recent git log, file tree, and the conversation so far (your earlier questions and the user's answers).

Reply with JSON only, one of:
{"status":"question","comment":"<2-4 sentences: your read of where the project stands>","questions":["...", "..."]}   (max 3 questions, only what you truly need)
{"status":"plan","comment":"<2-4 sentences: assessment + what to watch>","actions":[{"title":"...","why":"...","effort":"S|M|L"}]}   (3-5 concrete actions, ordered)

Effort: S = under an hour, M = half to one day, L = several days.
Actions marked done:true in earlier plans are finished — never repeat them, build on them. "note" on an action is user feedback on that action.
Ask questions only if the answer would change the plan. If docs + git already tell you enough, give the plan.
Language: match the language of the user's answers if any, else Swedish.

{role}

=== PROJECT: {name} (lins: {lens}) ===
{context}
=== CONVERSATION ===
{history}
"""


def load_cfg():
    with open(CFG) as f:
        return json.load(f)


def save_cfg(cfg):
    with open(CFG, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def project(name):
    for p in load_cfg()["projects"]:
        if p["name"] == name:
            return p
    sys.exit(f"unknown project {name}")


def state_path(name):
    return os.path.join(STATE, f"{name}.json")


def load_state(name):
    try:
        with open(state_path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"history": []}


def save_state(name, st):
    os.makedirs(STATE, exist_ok=True)
    with open(state_path(name), "w") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)


def expand(paths, base):
    """Turn config 'extra' entries (file, glob, or dir) into file paths."""
    out = []
    for raw in paths:
        p = os.path.expanduser(raw)
        if not os.path.isabs(p):
            p = os.path.join(base, p)
        for hit in sorted(glob.glob(p)):
            if os.path.isdir(hit):
                out += sorted(glob.glob(os.path.join(hit, "*.md")) + glob.glob(os.path.join(hit, "*.json")))
            else:
                out.append(hit)
    return out


def gather(proj, lens="all"):
    """proj: config dict (or a bare path string, for tests)."""
    if isinstance(proj, str):
        proj = {"path": proj}
    path = os.path.expanduser(proj["path"])
    pats = sum((SOURCES[k] for k in (SOURCES if lens == "all" else [lens])), [])
    cap = PER_FILE
    files = []
    for pat in pats:
        files += sorted(glob.glob(os.path.join(path, pat)))
    if lens in ("market", "money", "all"):
        files = expand(proj.get("extra", []), path) + files
    out, seen = [], set()
    if proj.get("status"):
        out.append("--- läget just nu (från användaren) ---\n" + proj["status"])
    uniq = []
    for fp in files:
        real = os.path.realpath(fp)
        if real not in seen:
            seen.add(real); uniq.append(fp)
    # every source gets a fair share of the budget, so a few long files can't crowd the rest out
    cap = max(1500, min(cap, TOTAL // max(1, len(uniq))))
    for fp in uniq:
        try:
            txt = open(fp, errors="replace").read()[:cap]
        except OSError:
            continue
        out.append(f"--- {os.path.relpath(fp, path)} ---\n{txt}"
                   + ("\n…(kapad)" if len(txt) == cap else ""))
    if lens in ("code", "all"):
        log = subprocess.run(["git", "log", "-15", "--oneline", "--date=short", "--format=%ad %s"],
                             cwd=path, capture_output=True, text=True).stdout.strip()
        out.append("--- git log ---\n" + (log or "(no git)"))
        try:
            tree = sorted(e for e in os.listdir(path) if e not in SKIP and not e.startswith("."))
        except OSError:
            tree = []
        out.append("--- files ---\n" + " ".join(tree))
    return "\n\n".join(out)


def parse_officer(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    s, e = raw.find("{"), raw.rfind("}")
    d = json.loads(raw[s:e + 1])
    assert d.get("status") in ("question", "plan"), d
    return d


def run(name, lens="all"):
    lens = lens if lens in LENSES else "all"
    p = project(name)
    st = load_state(name)
    hist = "\n".join(
        (f"OFFICER: {json.dumps({k: v for k, v in h.items() if k not in ('role', 'ts')}, ensure_ascii=False)}"
         if h["role"] == "officer" else f"USER: {h['text']}")
        for h in st["history"]) or "(none yet)"
    prompt = (PROMPT.replace("{name}", name).replace("{lens}", lens).replace("{role}", ROLES[lens])
              .replace("{context}", gather(p, lens)).replace("{history}", hist))
    r = subprocess.run(["claude", "-p", "--output-format", "json"], input=prompt,
                       capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    d = parse_officer(json.loads(r.stdout).get("result", ""))
    d.update(role="officer", lens=lens, ts=time.strftime("%Y-%m-%d %H:%M"))
    st["history"].append(d)
    save_state(name, st)
    return d


def answer(name, text):
    st = load_state(name)
    st["history"].append({"role": "user", "text": text, "ts": time.strftime("%Y-%m-%d %H:%M")})
    save_state(name, st)


def mark_done(name, index, done, note=""):
    st = load_state(name)
    last = [h for h in st["history"] if h["role"] == "officer" and h["status"] == "plan"][-1]
    a = last["actions"][index]
    a["done"], a["note"] = bool(done), note
    st["history"].append({"role": "user", "ts": time.strftime("%Y-%m-%d %H:%M"),
                          "text": f"{'✔ Klart' if done else '↩ Ångrat'}: {a['title']}" + (f" — {note}" if note else "")})
    save_state(name, st)


def add(name, path):
    cfg = load_cfg()
    name = name.strip()
    if not name or any(p["name"] == name for p in cfg["projects"]):
        raise ValueError(f"finns redan eller tomt namn: {name!r}")
    if not os.path.isdir(os.path.expanduser(path)):
        raise ValueError(f"ingen mapp: {path}")
    cfg["projects"].append({"name": name, "path": path, "tracked": True, "status": "", "extra": []})
    save_cfg(cfg)


def full_state():
    cfg = load_cfg()
    return {"projects": cfg["projects"], "state": {p["name"]: load_state(p["name"]) for p in cfg["projects"]}}


class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            return self._json(full_state())
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(open(os.path.join(HERE, "dashboard.html"), "rb").read())

    def do_POST(self):
        o = self.headers.get("Origin", "")
        if o and not o.startswith(("http://localhost", "http://127.0.0.1")):
            return self._json({"error": "forbidden"}, 403)  # block cross-site POSTs from other tabs
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or b"{}") or b"{}")
        try:
            if self.path == "/api/run":
                # ponytail: sync run blocks the request; thread it if you want parallel projects
                return self._json(run(body["name"], body.get("lens", "all")))
            if self.path == "/api/answer":
                answer(body["name"], body["text"]); return self._json({"ok": True})
            if self.path == "/api/done":
                mark_done(body["name"], int(body["index"]), body.get("done", True), body.get("note", "")); return self._json({"ok": True})
            if self.path == "/api/add":
                add(body["name"], body["path"]); return self._json({"ok": True})
            if self.path == "/api/status":
                cfg = load_cfg()
                for p in cfg["projects"]:
                    if p["name"] == body["name"]:
                        p["status"] = body["text"]
                save_cfg(cfg); return self._json({"ok": True})
            if self.path == "/api/track":
                cfg = load_cfg()
                for p in cfg["projects"]:
                    if p["name"] == body["name"]:
                        p["tracked"] = bool(body["tracked"])
                save_cfg(cfg); return self._json({"ok": True})
            self._json({"error": "unknown"}, 404)
        except Exception as ex:  # surface to UI
            self._json({"error": str(ex)}, 500)

    def log_message(self, *a):
        pass


def test():
    import tempfile
    d = tempfile.mkdtemp()
    open(os.path.join(d, "README.md"), "w").write("# hello")
    os.makedirs(os.path.join(d, "docs")); open(os.path.join(d, "docs/a.md"), "w").write("doc a")
    os.makedirs(os.path.join(d, "STRATEGY")); open(os.path.join(d, "STRATEGY/PRICING.md"), "w").write("pris 99")
    os.makedirs(os.path.join(d, "extras")); open(os.path.join(d, "extras/comp.md"), "w").write("konkurrent X")
    proj = {"path": d, "status": "12 kunder, 40k MRR", "extra": ["extras"]}
    ctx = gather(proj, "all")
    assert "# hello" in ctx and "doc a" in ctx and "(no git)" in ctx, ctx
    assert "12 kunder" in ctx and "pris 99" in ctx and "konkurrent X" in ctx, ctx
    m = gather(proj, "market")
    assert "pris 99" in m and "konkurrent X" in m and "git log" not in m and "doc a" not in m, m
    q = parse_officer('```json\n{"status":"question","comment":"c","questions":["q1"]}\n```')
    assert q["questions"] == ["q1"]
    p = parse_officer('text before {"status":"plan","comment":"c","actions":[{"title":"t","why":"w","effort":"S"}]}')
    assert p["actions"][0]["effort"] == "S"
    global STATE; STATE = os.path.join(d, "state")
    p["role"] = "officer"; save_state("x", {"history": [p]})
    mark_done("x", 0, True, "n")
    st = load_state("x")
    assert st["history"][0]["actions"][0]["done"] and st["history"][0]["actions"][0]["note"] == "n"
    assert st["history"][-1]["text"].startswith("✔ Klart: t — n")
    global CFG; CFG = os.path.join(d, "projects.json"); save_cfg({"projects": []})
    add("x", d)
    assert load_cfg()["projects"][0]["name"] == "x"
    for bad in [("x", d), ("y", d + "/nope")]:
        try: add(*bad); assert False
        except ValueError: pass
    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1:] or ["serve"]
    if cmd[0] == "run":
        lens = cmd[-1] if len(cmd) > 1 and cmd[-1] in LENSES else "all"
        args = [a for a in cmd[1:] if a not in LENSES]
        names = args or [p["name"] for p in load_cfg()["projects"] if p["tracked"]]
        for n in names:
            print(f"== {n} ({lens})"); print(json.dumps(run(n, lens), indent=2, ensure_ascii=False))
    elif cmd[0] == "answer":
        answer(cmd[1], " ".join(cmd[2:]))
    elif cmd[0] == "add":
        add(cmd[1], cmd[2])
    elif cmd[0] == "test":
        test()
    else:
        port = int(cmd[1]) if len(cmd) > 1 else 8765
        print(f"agentoffice → http://localhost:{port}")
        HTTPServer(("127.0.0.1", port), H).serve_forever()
