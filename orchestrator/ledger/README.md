---
tags: [orchestrator, ledger, costs, reference]
created: 2026-08-20
---

# ledger — what each product costs, and which key it spends on

Two jobs, one directory, because they are the same question asked twice: *whose
money is this?*

| File | What it is |
|---|---|
| `products.json` | The registry. Every product's subscriptions, the shared pools it draws on, and the **label** of the key it uses for each. Edit this, never the prose. |
| `ledger.py` | Reports per-product cost, snapshots pool balances, reconciles attributed against actually-drawn, and **generates Part 2** of [[01-Knowledge Base/Infrastructure Ledger\|Infrastructure Ledger]]. |
| `keys.py` | The only thing that reads `keys.md`. Resolves a provider + project to the one key that caller is entitled to, and refuses to fall back. |
| [[05-Orchestrator/ledger/keys.example|keys.example.md]] | The format. Copy this shape. |
| `keys.md` | **Not in git.** Live keys, written by the owner. |
| `pool-history.jsonl` | Daily balance snapshots. The only permanent record of the drawdown curve — OpenRouter's own history reaches back 30 days. |

```bash
python3 -m ledger.ledger report --paying-only   # what each product costs
python3 -m ledger.ledger reconcile              # attributed vs actually drawn
python3 -m ledger.ledger render                 # rewrite the prose ledger
python3 -m ledger.keys check                    # does every product have its key
```

**The rules live in [[CLAUDE|CLAUDE.md]]**, under the key-routing section — this
file does not restate them, because a rule written twice drifts the first time
one copy is edited.

**The finding that produced all of this**, measured 2026-08-20: four products
shared one OpenRouter key and **94% of $25.27 of spend could not be traced to any
product**. Providers attribute spend by key; attribution missed is attribution
gone.
