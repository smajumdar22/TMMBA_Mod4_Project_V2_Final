# Ticket Triage Tool — v1

A CLI tool that reads sprint tickets and team bandwidth, then suggests
a priority + one-line reasoning per ticket — **Recommend-only**, never
auto-applied. A human reviews the terminal output and manually confirms
by editing `human_confirmed_priority` in `tickets.csv`.

## Architecture (locked decisions from the Living Document)

- **Two source files**, not one: `tickets.csv` and `bandwidth.csv`, joined
  on `current_owner` / `team_member`. Keeps bandwidth data honestly
  timestamped instead of duplicated stale copies per ticket row.
- **One combined AI call per ticket** — urgency, sprint-board comparison,
  and bandwidth retrieval are reasoned about together in a single prompt,
  not three separate calls. Tradeoff: lower cost/latency and better
  cross-reasoning, at the cost of per-subsystem diagnosability when a
  suggestion is wrong.
- **Deterministic context checklist** (VIP account, blocking dependency,
  SLA deadline) is computed in code from real metadata fields — never
  inferred by the AI. This is the direct fix for Failure Mode #2
  (the AI missing context it can't see in the raw description).
- **Vagueness check runs before the AI call** and forces a LOW confidence
  flag on sparse tickets, regardless of what the model itself reports —
  this is a deterministic override, not a suggestion to the model.
  Fix for Failure Mode #4.
- **Full reasoning trace is logged** to `reasoning_log.jsonl`, permissions
  restricted to owner (`chmod 600`) since ticket text can contain
  sensitive account/customer data. This supports the A3 eval process and
  the Failure Mode #5 audit (checking misses for shared phrasing patterns).
- **No write-back.** `human_confirmed_priority` is edited by hand. This is
  a deliberate v1 scope cut — see the Cut List (C4) for the friction
  tradeoff this creates at scale.

## Setup

```bash
pip install anthropic --break-system-packages
export ANTHROPIC_API_KEY=your_key_here
```

## Run (CLI)

```bash
python3 triage.py
```

## Run (Web UI)

```bash
pip install flask --break-system-packages
python3 app.py
```

Then open `http://localhost:5050`. This is a thin display layer over the
same `process_ticket()` pipeline used by the CLI — no duplicated logic,
so the two surfaces can't silently drift apart. Still Recommend-only:
the web UI does not write `human_confirmed_priority` back to
`tickets.csv` — that stays a manual edit for v1.

## Files

- `tickets.csv` — ticket data (sample data included; replace with real export)
- `bandwidth.csv` — team capacity data (sample data included)
- `triage.py` — the tool itself
- `reasoning_log.jsonl` — generated on first run; full AI reasoning trace, access-restricted

## Known v1 limitations (documented, not hidden)

- No live/real-time trigger — on-demand only (see Section 5, C2)
- Manual confirm creates friction at scale; may silently stop happening
  under backlog volume (see Section 5, C3 discussion)
- Combined-call architecture means a wrong suggestion can't be cleanly
  attributed to one of the three reasoning inputs without reading the
  full trace in `reasoning_log.jsonl`
