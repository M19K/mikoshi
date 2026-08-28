#!/usr/bin/env python3
"""
mcp_server.py — Mikoshi as something any agent can call.

Until now the brain was a set of scripts one person ran by hand. Nothing could
ask it a question. This exposes it over MCP, the protocol Claude Code, Codex,
Cursor and Hermes all already speak, so an agent working any project can query
the vault instead of grepping it and hoping.

Six tools, chosen to be the smallest set that covers real use:

  recall           search the vault: keyword + meaning, fused by rank
  entity           everything the vault knows about one tool, model or company
  queue            who is working on what, what is blocked, what needs the owner
  record_decision  what the owner chose and what he turned down
  record_learning  what an agent found out the hard way
  digest           the most recent ingestion run

**Written against the wire protocol directly, with no MCP library.** The vault's
whole promise is that it needs a text editor rather than a stack, and JSON-RPC
over stdin is about eighty lines. A dependency here would be the largest thing
in the repository.

    python3 05-Orchestrator/mcp_server.py --selftest    # verify without a client
    python3 05-Orchestrator/mcp_server.py               # serve on stdio

Register with Claude Code:
    claude mcp add mikoshi -- python3 /path/to/your-vault/05-Orchestrator/mcp_server.py
"""
import json
import pathlib
import subprocess
import sys

# **Windows consoles default to cp1252 and cannot encode the characters this
# codebase prints** — the log separator `·`, the em dash, and the `→` in every
# "here is the fix" line. On 2026-08-23 CI showed `record.py` dying on its own
# arrow, which meant the WRITE PATH was broken on Windows while every other
# check passed. Done at package import so a new script in here inherits it
# rather than having to remember; fixing twenty entry points one at a time is
# how the twenty-first gets missed.
import sys as _sys

for _s in (_sys.stdout, _sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


HERE = pathlib.Path(__file__).resolve().parent
VAULT = HERE.parent
VENV = VAULT / "02-Projects/delta/code/tools/.venv/bin/python3"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {"name": "answer",
     "description": "Ask the vault a question and get a written answer with every "
                    "claim pinned to a file and LINE NUMBER you can open. Unlike "
                    "`recall`, which hands back passages for you to read, this "
                    "synthesises — and it states who authored each cited line, so "
                    "you can tell what the owner DECIDED from what an agent merely "
                    "wrote. It also answers 'why not X?' from the 165 recorded "
                    "decisions, all of which carry the options that were rejected. "
                    "Citations are verified against the evidence actually "
                    "retrieved; invented ones are stripped. Runs entirely on a "
                    "local model, so it needs no API key. About 15 seconds. "
                    "Prefer this over `recall` for WHY questions and for anything "
                    "where you would otherwise assert something you have not read.",
     "inputSchema": {"type": "object", "required": ["question"], "properties": {
         "question": {"type": "string", "description": "A question in plain words."},
         "scope": {"type": "string",
                   "description": "A project folder name. Keeps the answer inside "
                                  "that project instead of the whole vault."},
         "k": {"type": "integer", "default": 6,
               "description": "How many source notes to draw evidence from."}}}},
    {"name": "why_not",
     "description": "What was this option's fate? Searches the REJECTED "
                    "alternatives of every recorded decision and returns the ones "
                    "that turned it down, with the date and the reason. Use before "
                    "proposing anything, because re-proposing something already "
                    "refused is the most common way this vault wastes the owner's time. "
                    "Instant — no model involved.",
     "inputSchema": {"type": "object", "required": ["option"], "properties": {
         "option": {"type": "string", "description": "The thing you are about to propose."},
         "project": {"type": "string", "description": "Optional project to narrow to."}}}},
    {"name": "recall",
     "description": "Search the Mikoshi vault. Combines exact keyword matching with "
                    "meaning-based search over passages. Use for any question about what "
                    "the vault knows, what was decided, or how something works. Measured "
                    "on a 20-question set: right answer first 85% of the time, in the top "
                    "five 95%.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "A question or topic, in plain words."},
         "k": {"type": "integer", "default": 5, "description": "How many results."},
         "deep": {"type": "boolean", "default": False,
                  "description": "Score results with the model as well. 18x slower and "
                                 "measured to rank the single best answer WORSE — only "
                                 "use when you will read all five results."}}}},
    {"name": "entity",
     "description": "Everything the vault knows about one named thing — a tool, model, "
                    "company or person — including every note that mentions it.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string", "description": "Exact or partial name, e.g. 'sqlite-vec'."}}}},
    {"name": "queue",
     "description": "Current state across every project: who holds what, what is blocked, "
                    "what is waiting on the owner, open handoffs and notices. Read this before "
                    "starting work in any project.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "record_decision",
     "description": "Record something the owner chose, and what he turned down. Call this the "
                    "moment he states a preference — chat is not storage, and the rejected "
                    "options are the half that stops the next agent re-proposing them.",
     "inputSchema": {"type": "object", "required": ["project", "chose"], "properties": {
         "project": {"type": "string"}, "chose": {"type": "string"},
         "over": {"type": "string", "description": "Comma-separated rejected options."},
         "why": {"type": "string", "description": "His reason, in his words where possible."}}}},
    {"name": "record_learning",
     "description": "Record something you found out the hard way: a trap, a technique, a "
                    "limit, or a correction to something the vault claims. Another project "
                    "may hit the same thing.",
     "inputSchema": {"type": "object", "required": ["project", "key", "insight"], "properties": {
         "project": {"type": "string"},
         "key": {"type": "string", "description": "Short stable slug, e.g. 'macos-tcc-binds-to-signature'."},
         "insight": {"type": "string"},
         "type": {"type": "string", "enum": ["pitfall", "technique", "constraint", "correction"]},
         "files": {"type": "string", "description": "Comma-separated paths this touches."},
         "confidence": {"type": "integer", "description": "1-10."}}}},
    {"name": "digest",
     "description": "The most recent ingestion run: what was filed, what only appeared, "
                    "what was dropped and why, and which sources could not be reached.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def run(args, timeout=300):
    """Everything heavy runs in the funnel's venv, which holds sqlite-vec."""
    py = str(VENV) if VENV.exists() else sys.executable
    r = subprocess.run([py, *args], capture_output=True, text=True,
                       cwd=str(HERE), timeout=timeout)
    return (r.stdout or r.stderr or "").strip()


def t_answer(a):
    cmd = ["-m", "synth.answer", a["question"], "-k", str(a.get("k", 6))]
    if a.get("scope"):
        cmd += ["--scope", a["scope"]]
    return run(cmd, timeout=420)


def t_why_not(a):
    cmd = ["-m", "synth.decisions", a["option"]]
    if a.get("project"):
        cmd += ["--project", a["project"]]
    return run(cmd, timeout=60)


def t_recall(a):
    cmd = ["-m", "funnel.retrieve", a["query"], "-k", str(a.get("k", 5))]
    if a.get("deep"):
        cmd.append("--rerank")
    return run(cmd)


def t_entity(a):
    ent = VAULT / "01-Knowledge Base" / "Entities"
    if not ent.exists():
        return "No entity pages yet. Run: python3 -m funnel.entities --apply"
    want = a["name"].lower().replace(" ", "-")
    hits = [p for p in ent.glob("*.md") if want in p.stem] or \
           [p for p in ent.glob("*.md") if want.split("-")[0] in p.stem]
    if not hits:
        return f"Nothing known about {a['name']!r}. Try `recall` instead."
    return "\n\n---\n\n".join(p.read_text(encoding="utf-8")[:2500] for p in hits[:3])


def t_queue(_a):
    p = HERE / "Queue.md"
    return p.read_text(encoding="utf-8") if p.exists() else "No Queue.md"


def t_digest(_a):
    d = sorted((HERE / "digests").glob("*.md")) if (HERE / "digests").exists() else []
    return d[-1].read_text(encoding="utf-8")[:6000] if d else "No digest yet."


def t_record_decision(a):
    cmd = ["record.py", "decision", a["project"], "--chose", a["chose"]]
    for flag, key in (("--over", "over"), ("--why", "why")):
        if a.get(key):
            cmd += [flag, a[key]]
    return run(cmd, timeout=30)


def t_record_learning(a):
    cmd = ["record.py", "learning", a["project"], "--key", a["key"],
           "--insight", a["insight"]]
    for flag, key in (("--type", "type"), ("--files", "files")):
        if a.get(key):
            cmd += [flag, str(a[key])]
    if a.get("confidence"):
        cmd += ["--confidence", str(a["confidence"])]
    return run(cmd, timeout=30)


HANDLERS = {"answer": t_answer, "why_not": t_why_not, "recall": t_recall, "entity": t_entity, "queue": t_queue, "digest": t_digest,
            "record_decision": t_record_decision, "record_learning": t_record_learning}


def handle(req):
    """One JSON-RPC message in, at most one out. Notifications get None."""
    method, rid = req.get("method"), req.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mikoshi", "version": "1.0.0"}}}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None                      # notifications carry no id and get no reply

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        fn = HANDLERS.get(name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"no such tool: {name}"}}
        try:
            text = fn(params.get("arguments") or {})
        except Exception as e:                      # a crash must not kill the server
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True}}
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": text or "(no output)"}]}}

    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unknown method: {method}"}}


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def selftest():
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))

    print("handshake")
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("initialize returns a protocol version",
          r["result"]["protocolVersion"] == PROTOCOL_VERSION)
    check("initialized notification is silent",
          handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)

    print("\ntools")
    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in r["result"]["tools"]]
    # Derived from TOOLS rather than a typed number. The literal 6 here went
    # stale the moment `answer` and `why_not` were added, and a self-test that
    # fails on a correct change trains you to ignore it.
    check("every registered tool is advertised and handled",
          set(names) == {t["name"] for t in TOOLS} == set(HANDLERS),
          f"{len(names)}: " + ", ".join(names))
    check("every tool has a schema",
          all("inputSchema" in t and "description" in t for t in r["result"]["tools"]))

    print("\ncalls")
    def call(name, args):
        return handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                       "params": {"name": name, "arguments": args}})["result"]["content"][0]["text"]

    q = call("queue", {})
    check("queue returns the live board", "## Active" in q, f"{len(q)} chars")
    e = call("entity", {"name": "agentmail"})
    check("entity finds a known tool", "AgentMail" in e)
    e2 = call("entity", {"name": "zzzznotathing"})
    check("unknown entity fails gracefully", "Nothing known" in e2)
    d = call("digest", {})
    check("digest returns the last run", "Digest" in d or "No digest" in d)

    print("\nerrors")
    bad = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "nope", "arguments": {}}})
    check("unknown tool is a clean error", "error" in bad)
    unk = handle({"jsonrpc": "2.0", "id": 4, "method": "wat"})
    check("unknown method is a clean error", "error" in unk)

    # This is a SMOKE test: does the plumbing work. Whether the results are any
    # good is `funnel.evals`' job, and conflating the two is what produced a
    # hand-written assertion loose enough to pass a wrong answer.
    print("\nsearch (runs the real pipeline)")
    s = call("recall", {"query": "which vector store did we choose", "k": 3})
    check("recall returns ranked results", s.count(".md") >= 2, s.splitlines()[-1][:60] if s else "")

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else serve())
