#!/usr/bin/env python3
"""
ledger.py — what each product costs, including its share of the shared pools.

**The gap this closes.** The Infrastructure Ledger answered "what do we pay for"
and "how does each platform bill". It could not answer *"what does gamma cost"*,
because the answer is partly a subscription that is clearly gamma's and partly a
slice of one OpenRouter balance that four products draw on. A single prepaid
balance with four consumers is not a cost you can read off a bill; it has to be
attributed. [@owner · 2026-08-20]

**Two tiers of attribution, and the difference is never hidden.**

  Tier A — live since 2026-08-20. One OpenRouter key per product, and a
                    management key that can read `/keys`. Each key's lifetime
                    usage is reported by OpenRouter, and the `hash` it returns is
                    the sha256 prefix of the key itself — the same value
                    `keys.fingerprint()` computes. So labels map to OpenRouter's
                    own accounting with nothing to configure and nothing to trust.
  Tier B — fallback. No management key: attribution falls back to what each
                    project recorded itself in `Costs.jsonl`, and **the shortfall
                    against the pool's real drawdown is reported as
                    `unattributed`** — its own line, never spread across products
                    to make the columns add up. A number that looks complete and
                    is not is worse than a visible hole.

**The $25.27 drawn before 2026-08-20 is unattributable by construction** and is
treated as an opening balance. It was spent through one shared key; OpenRouter's
history reaches back 30 days, so it can never be split. Do not try.

**Secrets.** This never prints a key. The pool balance is read with the
`management` key resolved through `ledger.keys` — chosen over the connector
because an OAuth-minted key expires and a management key does not, so the ledger
never stops reporting because something needs reconnecting. Falls back to
`OPENROUTER_API_KEY` in the environment, then to the last snapshot on disk,
which is always stated as such.

    python3 -m ledger.ledger report          # per-product costs
    python3 -m ledger.ledger pool            # shared pool balances
    python3 -m ledger.ledger snapshot        # record today's pool state
    python3 -m ledger.ledger reconcile       # attributed vs actually drawn
    python3 -m ledger.ledger render          # rewrite the prose ledger's Part 2

Run from `05-Orchestrator/`.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ORCH = HERE.parent
VAULT = ORCH.parent
PROJECTS = VAULT / "02-Projects"
REGISTRY = HERE / "products.json"
SNAPSHOTS = HERE / "pool-history.jsonl"
LEDGER_MD = VAULT / "01-Knowledge Base" / "Infrastructure Ledger.md"

OPENROUTER = "https://openrouter.ai/api/v1"


def today() -> str:
    return dt.date.today().isoformat()


def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- pool state

def openrouter_live():
    """Balance and lifetime drawdown, or None if no key can be resolved.

    **Three sources, in order, and the order is the point.** The `management`
    key in `keys.md` comes first because it is the only one that never expires:
    keys minted through an app's OAuth flow carry whatever `expires_at` that app
    asked for — Claude Code's OpenRouter connector asks for about a week — so a
    ledger that reads the balance through the connector silently stops reporting
    the day that key lapses, and needs a human to reconnect it. Reading through
    `management` removes that dependency entirely. [@owner · 2026-08-20]

    Falls back to `OPENROUTER_API_KEY` in the environment, then to nothing —
    at which point a reading taken via the connector can still be passed to
    `snapshot --loaded/--used` by hand."""
    key, source = None, None
    try:
        from .keys import resolve, KeyError_
        try:
            key = resolve("openrouter", label="management")
            source = "openrouter REST /credits (management key)"
        except KeyError_:
            key = None
    except ImportError:
        pass
    if not key:
        key = os.environ.get("OPENROUTER_API_KEY")
        source = "openrouter REST /credits (OPENROUTER_API_KEY)" if key else None
    if not key:
        return None
    req = urllib.request.Request(f"{OPENROUTER}/credits",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())["data"]
    except (urllib.error.URLError, KeyError, ValueError):
        return None
    return {"loaded_usd": float(d["total_credits"]),
            "used_usd": float(d["total_usage"]),
            "remaining_usd": float(d["total_credits"]) - float(d["total_usage"]),
            # Which key actually read it, so the row's provenance is observed
            # rather than assumed. The automatic branch of `snapshot` wrote no
            # `how` and no `by` at all until 2026-09-02, so an unattended run
            # recorded a number nobody could trace to a reader.
            # [@claude-code/maintenance · 2026-09-02]
            "source": source}


def snapshots() -> list:
    if not SNAPSHOTS.exists():
        return []
    return [json.loads(l) for l in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if l.strip()]


def latest_snapshot(pool: str = "openrouter"):
    rows = [s for s in snapshots() if s.get("pool") == pool]
    return rows[-1] if rows else None


def burn(pool: str = "openrouter", window_days: int = 7):
    """Burn rate derived from pool-history.jsonl, never asserted.

    Returns (rate_per_day, days_spanned, last_delta, last_delta_days) or None.
    Written 2026-08-27: the Open Board carried "about two days" for four days
    after the rate it was computed from had stopped. The data to re-derive it
    was in this file the whole time; nothing read it. A runway that is typed
    once is a claim, and a claim does not notice when it stops being true.

    Extended 2026-09-02: the averages carry the same fault one window down.
    Both the 7d and 5d figures are dominated by whichever days were busy, so
    when the largest spender stops they keep quoting the rate from before it
    stopped — on 2026-09-02 both said one day left while the two days since
    the previous reading had drawn $0.39, about 29 days. `last_delta` was
    already returned here and nothing turned it into a runway; reconcile()
    now does.
    """
    from datetime import date as _date

    def d(s):
        return _date(*(int(x) for x in s.split("-")))

    rows = [s for s in snapshots() if s.get("pool") == pool]
    if len(rows) < 2:
        return None
    rows.sort(key=lambda s: (s["date"], s.get("ts", "")))
    last = rows[-1]

    # one reading per day — the last of that day — so a twice-run routine
    # does not read as a zero-length day.
    by_day = {}
    for s in rows:
        by_day[s["date"]] = s
    days = sorted(by_day)

    span = [x for x in days if (d(last["date"]) - d(x)).days <= window_days]
    if len(span) < 2:
        return None
    first = by_day[span[0]]
    spanned = (d(last["date"]) - d(span[0])).days
    if spanned <= 0:
        return None
    rate = (last["used_usd"] - first["used_usd"]) / spanned

    prev = by_day[days[-2]]
    gap = (d(last["date"]) - d(days[-2])).days
    return rate, spanned, last["used_usd"] - prev["used_usd"], gap


def pool_state(pool: str = "openrouter"):
    """(state, how) — live if a key is present, else the last snapshot, else None."""
    if pool == "openrouter":
        live = openrouter_live()
        if live:
            return live, "live"
    snap = latest_snapshot(pool)
    if snap:
        return {k: snap[k] for k in ("loaded_usd", "used_usd", "remaining_usd")}, f"snapshot {snap['date']}"
    return None, "no reading"


# ------------------------------------------------------- recorded attribution

def recorded_costs(project: str) -> list:
    p = PROJECTS / project / "Costs.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def openrouter_by_key():
    """{label: usd} straight from OpenRouter, or None without a management key.

    **This is Tier A — the real thing.** OpenRouter's `/keys` reports lifetime
    usage per key, and the hash it returns is the sha256 prefix of the key
    itself — the same value `keys.fingerprint()` computes locally. So a label in
    `keys.md` maps to OpenRouter's own accounting with no configuration, no
    naming convention to keep in step, and nothing to trust. [@owner · 2026-08-20]"""
    try:
        from .keys import load, fingerprint, KeyError_
        try:
            keys = load()
        except KeyError_:
            return None
    except ImportError:
        return None
    mgmt = keys.get("openrouter", {}).get("management")
    if not mgmt:
        return None
    req = urllib.request.Request(f"{OPENROUTER}/keys",
                                 headers={"Authorization": f"Bearer {mgmt}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode()).get("data", [])
    except (urllib.error.URLError, ValueError):
        return None

    by_hash = {str(r.get("hash", "")): float(r.get("usage") or 0) for r in rows}
    out = {}
    for label, value in keys["openrouter"].items():
        fp = fingerprint(value)
        for h, usd in by_hash.items():
            if h.startswith(fp):
                out[label] = round(usd, 6)
                break
    return out


def attributed(pool_service: str = "OpenRouter") -> dict:
    """Per-product spend against a pool. Read from the provider when possible.

    Falls back to what each project recorded itself in `Costs.jsonl` — exact for
    the runs that were recorded, blind to any that were not. `reconcile` is what
    turns that blindness into a number rather than letting it hide."""
    if pool_service.lower() == "openrouter":
        live = openrouter_by_key()
        if live is not None:
            reg = registry()["products"]
            out = {}
            for name, p in reg.items():
                label = (p.get("keys") or {}).get("openrouter")
                if label and live.get(label):
                    out[name] = live[label]
            return out

    out = {}
    for name in registry()["products"]:
        total = 0.0
        for row in recorded_costs(name):
            if row.get("service", "").lower() == pool_service.lower():
                try:
                    total += float(row.get("monthly_usd", 0))
                except (TypeError, ValueError):
                    pass
        if total:
            out[name] = round(total, 4)
    return out


def attribution_tier() -> str:
    return "read from OpenRouter per key" if openrouter_by_key() is not None \
        else "self-reported in Costs.jsonl"


# ------------------------------------------------------------------ commands

def cmd_pool(_):
    reg = registry()
    for pool, meta in reg["pools"].items():
        state, how = pool_state(pool)
        users = [n for n, p in reg["products"].items() if pool in p.get("pools", [])]
        print(f"{meta['label']} — {meta['billing']}")
        if state:
            print(f"  loaded    ${state['loaded_usd']:.2f}")
            print(f"  used      ${state['used_usd']:.2f}")
            print(f"  remaining ${state['remaining_usd']:.2f}   ({how})")
        elif pool == "openrouter":
            print(f"  no reading — add a `management` key to keys.md, or pass a")
            print(f"  connector reading to `snapshot --loaded/--used` ({how})")
        else:
            print(f"  no balance reading — {meta['label']} is not wired for automatic")
            print(f"  readings; its cost is the subscription row in products.json")
        print(f"  drawn on by {len(users)}: {', '.join(sorted(users))}")
    return 0


def cmd_snapshot(args):
    """Record today's pool state.

    Two ways in, because an agent has two legitimate ways to see spend and
    neither involves holding a key. Either `OPENROUTER_API_KEY` is in the
    environment, or an agent read the balance through the OpenRouter connector
    and passes the two numbers with `--loaded/--used`. The second is the normal
    path for a Claude Code session: the connector is exactly the read access
    CLAUDE.md grants, and it means no key is ever fetched, echoed, or stored."""
    if args.loaded is not None and args.used is not None:
        row = {"date": today(), "pool": "openrouter",
               "ts": dt.datetime.now().isoformat(timespec="seconds"),
               "loaded_usd": args.loaded, "used_usd": args.used,
               "remaining_usd": round(args.loaded - args.used, 6),
               "how": args.how, "by": args.by}
        with SNAPSHOTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"openrouter: ${args.used:.2f} used of ${args.loaded:.2f} "
              f"recorded for {today()} (read via {args.how} by {args.by})")
        return 0

    # Only `openrouter` has a balance endpoint wired. `hume` and `openai` are
    # subscription/usage-billed and have no reader here, so the loop used to
    # print, for each of them, that setting OPENROUTER_API_KEY would fix it and
    # that the fallback was `snapshot --loaded/--used` — a command whose branch
    # hardcodes `"pool": "openrouter"`, so following the instruction would have
    # written a hume balance into the OpenRouter history. Two false claims a
    # day, every day, in the output of the routine whose job is the money.
    # A pool with no reader is not a failed reading; say what it actually is.
    # [@claude-code/maintenance · 2026-09-02]
    READABLE = {"openrouter"}

    n = 0
    for pool in registry()["pools"]:
        if pool not in READABLE:
            print(f"{pool}: no balance endpoint wired — subscription/usage-billed, "
                  f"recorded by hand in ledger/products.json. Nothing to snapshot.")
            continue
        state = openrouter_live()
        if not state:
            print(f"{pool}: no live reading — the `management` key did not resolve and")
            print(f"  OPENROUTER_API_KEY is unset. Read total_credits and total_usage")
            print(f"  through the OpenRouter connector if this session has one, and pass them:")
            print(f"    python3 -m ledger.ledger snapshot --loaded <total_credits> --used <total_usage> \\")
            print(f"        --by \"@claude-code/maintenance\" --how \"openrouter connector\"")
            continue
        source = state.pop("source", None)
        row = {"date": today(), "pool": pool,
               "ts": dt.datetime.now().isoformat(timespec="seconds"), **state,
               "how": args.how if "unrecorded" not in args.how else (source or "unrecorded"),
               "by": args.by}
        # Store the per-key split alongside the pool total, not just the total.
        # `reconcile` reads the split live and keeps none of it, so on 2026-09-09
        # the only way to learn WHICH product had drawn $2.99 overnight was to
        # grep a figure out of a prose log entry written two days earlier. The
        # 30-day window that justifies snapshotting the pool at all applies to
        # the per-key numbers identically — unstored, they are gone for good.
        # Same management key, already resolved; one extra call.
        # [@claude-code/maintenance · 2026-09-09]
        by_key = openrouter_by_key()
        if by_key:
            row["by_key_usd"] = by_key
        with SNAPSHOTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{pool}: ${state['used_usd']:.2f} used of ${state['loaded_usd']:.2f} recorded for {today()}")
        n += 1
    return 0 if n else 1


def cmd_report(args):
    reg = registry()
    att = attributed()
    rows, fixed_total = [], 0.0
    for name, p in reg["products"].items():
        subs = p.get("subscriptions", [])
        fixed = sum(float(s.get("monthly_usd", 0)) for s in subs)
        paid = [s for s in subs if float(s.get("monthly_usd", 0)) > 0]
        usage = att.get(name)
        # A product that draws on a shared pool costs money even when it has
        # recorded none — that is precisely the case the unattributed balance is
        # hiding, so hiding it here too would be the same mistake twice.
        if args.paying_only and not fixed and not usage and not p.get("pools"):
            continue
        fixed_total += fixed
        rows.append((name, p, fixed, paid, usage))

    print(f"Per-product cost — {today()}\n")
    print(f"  {'product':<28}{'fixed /mo':>11}{'pool spend':>15}  depends on")
    print("  " + "-" * 86)
    for name, p, fixed, paid, usage in sorted(rows, key=lambda r: -r[2]):
        parts = [s["service"] for s in paid]
        parts += [reg["pools"][x]["label"] for x in p.get("pools", [])]
        dep = ", ".join(parts) or "—"
        f = f"${fixed:.2f}" if fixed else "—"
        u = f"${usage:.2f}" if usage else ("none recorded" if p.get("pools") else "—")
        print(f"  {p['display']:<28}{f:>11}{u:>15}  {dep}")
    print("  " + "-" * 86)
    print(f"  {'fixed monthly, all products':<28}{'$' + format(fixed_total, '.2f'):>11}")

    state, how = pool_state("openrouter")
    if state:
        named = sum(att.values())
        print(f"\n  OpenRouter pool: ${state['used_usd']:.2f} drawn, "
              f"${state['remaining_usd']:.2f} left ({how})")
        print(f"  of that, ${named:.2f} is attributed to a product and "
              f"${state['used_usd'] - named:.2f} is not. `reconcile` explains why.")
    return 0


def cmd_reconcile(_):
    reg = registry()
    att = attributed()
    tier = attribution_tier()
    state, how = pool_state("openrouter")
    print(f"OpenRouter reconciliation — {today()}\n")
    if not state:
        print("  No pool reading available. Add a `management` key to keys.md.")
        return 1

    baseline = reg["pools"]["openrouter"].get("opening_balance_usd", 0.0)
    named = sum(att.values())
    live = openrouter_by_key() or {}
    non_product = sum(v for k, v in live.items()
                      if k not in {(p.get("keys") or {}).get("openrouter")
                                   for p in reg["products"].values()})

    print(f"  drawn from the pool      ${state['used_usd']:>9.2f}   ({how})")
    print(f"  attribution              {tier}")
    print()
    for name, amt in sorted(att.items(), key=lambda kv: -kv[1]):
        print(f"    {reg['products'][name]['display']:<22} ${amt:>9.4f}")
    for label, amt in sorted(live.items(), key=lambda kv: -kv[1]):
        if label in ("mikoshi-internal", "owner-personal") and amt:
            print(f"    {label:<22} ${amt:>9.4f}   not a product")
    print(f"  attributed since keys    ${named + non_product:>9.4f}")
    print(f"  opening balance          ${baseline:>9.2f}   spent through the one")
    print(f"  {'':<24}{'':>10}   shared key before 2026-08-20 —")
    print(f"  {'':<24}{'':>10}   unattributable by construction")

    b = burn("openrouter")
    if b:
        rate, spanned, last_delta, last_gap = b
        print()
        print(f"  burn, last {spanned}d          ${rate:>9.4f}   per day, from pool-history.jsonl")
        # A single average hides a rate that changed. Print a short window beside
        # the long one: when the two disagree, the average is the misleading half.
        b3 = burn("openrouter", 5)
        recent = None
        if b3 and abs(b3[0] - rate) > 0.01:
            recent = b3[0]
            print(f"  burn, last {b3[1]}d          ${b3[0]:>9.4f}   per day — "
                  f"{'well below' if b3[0] < rate else 'above'} the {spanned}d average, "
                  f"so the rate CHANGED")
        label = "since the previous reading" if last_gap != 1 else "in the last day"
        print(f"  most recent step         ${last_delta:>9.4f}   {label}"
              + (f" ({last_gap}d apart)" if last_gap != 1 else ""))
        rates = [("the %dd average" % spanned, rate)]
        if recent is not None:
            rates.append(("the recent rate", recent))
        # Both averages are dominated by whichever days were busy, so when a
        # spender stops they keep quoting the rate from before it stopped. On
        # 2026-09-02 both printed 1d while the two days since the last reading
        # had drawn $0.39 — about 29d. The step is the only rate measured on
        # the present, and it was already computed here and never used.
        step_rate = last_delta / last_gap if last_gap else None
        if step_rate is not None and all(abs(step_rate - r) > 0.01
                                         for _, r in rates):
            rates.append(("the latest step", step_rate))
        for label, r in rates:
            if r > 0.005:
                print(f"  runway on {label:<15}{state['remaining_usd'] / r:>7.0f}d   "
                      f"on ${state['remaining_usd']:.2f} remaining")
            else:
                print(f"  runway on {label:<15}{'—':>7}    barely drawing")
        if len(rates) > 1:
            word = {2: "TWO", 3: "THREE"}.get(len(rates), str(len(rates)))
            print(f"  {word} runways because the rate changed. None is 'the' number —")
            print("  which one holds depends on whether the agents run. Say them all.")
        print("  Quote this, not a remembered rate — the two diverged for four days in August.")

    gap = state["used_usd"] - baseline - named - non_product
    if abs(gap) > 0.01:
        print(f"  UNEXPLAINED              ${gap:>9.4f}   investigate — this should be ~0")
    else:
        print(f"\n  Every dollar since 2026-08-20 is accounted for to a key.")
    return 0


# -------------------------------------------------------------- render prose

BEGIN = "<!-- BEGIN GENERATED — ledger.py render · do not hand-edit below -->"
END = "<!-- END GENERATED -->"


def cmd_render(_):
    """Rewrite the volatile half of the prose ledger from the registry.

    Part 2 went stale for three weeks while every fact around it moved, and the
    file's own `updated:` header said 29 July while its rows said 19 August. A
    section that is typed by hand and read as current is a trap; this makes it a
    projection of the data instead, stamped with the day it was produced."""
    reg = registry()
    att = attributed()
    state, how = pool_state("openrouter")
    L = [BEGIN, "",
         f"*Generated {today()} by `05-Orchestrator/ledger/ledger.py render` from "
         f"`ledger/products.json`. Do not hand-edit — edit the registry and re-render. "
         f"No figure here is a billing-API reading except the OpenRouter pool; the rest "
         f"are hand-entered from the record each row names.*", "",
         "### What each product costs", "",
         "| Product | Fixed monthly | Shared pool spend | Paid dependencies |",
         "|---|---|---|---|"]

    total = 0.0
    for name, p in sorted(reg["products"].items(),
                          key=lambda kv: -sum(float(s.get("monthly_usd", 0))
                                              for s in kv[1].get("subscriptions", []))):
        subs = p.get("subscriptions", [])
        fixed = sum(float(s.get("monthly_usd", 0)) for s in subs)
        total += fixed
        paid = [f"{s['service']} ({s['plan']})" for s in subs if float(s.get("monthly_usd", 0)) > 0]
        pools = [reg["pools"][x]["label"] for x in p.get("pools", [])]
        dep = " · ".join(paid + pools) or "none"
        usage = att.get(name)
        u = f"**${usage:.2f}**" if usage else ("nothing recorded" if pools else "—")
        L.append(f"| **{p['display']}** | {'**$' + format(fixed, '.2f') + '**' if fixed else '$0.00'} "
                 f"| {u} | {dep} |")
    L.append(f"| **Fixed total** | **${total:.2f}/mo** | | |")

    L += ["", "### The shared pool", ""]
    if state:
        named = sum(att.values())
        gap = state["used_usd"] - named
        L += [f"OpenRouter is one prepaid balance that **{len([1 for p in reg['products'].values() if p.get('pools')])} "
              f"products draw on**: ${state['loaded_usd']:.2f} loaded, "
              f"**${state['used_usd']:.2f} drawn**, ${state['remaining_usd']:.2f} left ({how}).", "",
              f"Of what has been drawn, **${named:.2f} is attributed to a product and "
              f"${gap:.2f} ({gap / state['used_usd'] * 100:.0f}%) is not** — and the unattributed "
              f"share is stated rather than spread, because a table that adds up by "
              f"assumption is worse than one with a visible hole.", "",
              "**Why the hole exists:** all four products send calls through one API key, so "
              "nothing at OpenRouter's end can tell whose a given call was. Only spend a "
              "project recorded itself appears as attributed. **The fix is one key per "
              "product plus a management key** — then `/api/v1/activity?api_key_hash=` "
              "reports each product's real number and none of this is trusted. Creating "
              "keys is an account change, so it is the owner's to do."]
    else:
        L.append("*No pool reading available — set `OPENROUTER_API_KEY` or run "
                 "`ledger.py snapshot`.*")

    L += ["", END]
    block = "\n".join(L)

    text = LEDGER_MD.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        pre = text[:text.index(BEGIN)]
        post = text[text.index(END) + len(END):]
        text = pre + block + post
    else:
        anchor = "### Where every project sits"
        i = text.index(anchor)
        text = text[:i] + block + "\n\n" + text[i:]

    # The header must never claim a date the body has outrun.
    text = "\n".join(
        (f"updated: {today()}" if l.startswith("updated:") else l)
        for l in text.split("\n"))
    LEDGER_MD.write_text(text, encoding="utf-8")
    print(f"Infrastructure Ledger Part 2 regenerated and stamped {today()}.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="per-product costs")
    r.add_argument("--paying-only", action="store_true", help="hide products that cost nothing")
    sub.add_parser("pool", help="shared pool balances")
    s = sub.add_parser("snapshot", help="record today's pool state")
    s.add_argument("--loaded", type=float, help="total credits loaded, read via the connector")
    s.add_argument("--used", type=float, help="total usage, read via the connector")
    s.add_argument("--by", default="@claude-code", help="who took the reading")
    # `how` was hardcoded to "openrouter connector" until 2026-08-29, which made
    # the field worthless: a scheduled run has no connector and reads the same
    # numbers off the REST endpoint, and the row claimed the connector anyway.
    # A provenance field that always says the same thing is not provenance.
    # [@claude-code/maintenance · 2026-08-29]
    #
    # Un-hardcoding it was not enough: the *default* was left at the old string,
    # so a run that simply omits the flag still writes the same false claim.
    # That is what happened on 2026-08-31 — the connector has been gone since
    # 2026-08-27, the reading came off REST, and the row said "connector".
    # The default is now the honest one; a caller that knows says so.
    # [@claude-code/maintenance · 2026-08-31]
    s.add_argument("--how", default="unrecorded — --how not passed",
                   help="where the reading came from — 'openrouter connector' "
                        "or 'openrouter REST /credits'")
    sub.add_parser("reconcile", help="attributed vs actually drawn")
    sub.add_parser("render", help="rewrite the prose ledger's Part 2")
    a = ap.parse_args()
    return {"report": cmd_report, "pool": cmd_pool, "snapshot": cmd_snapshot,
            "reconcile": cmd_reconcile, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
