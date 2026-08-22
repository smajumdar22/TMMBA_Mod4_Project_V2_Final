#!/usr/bin/env python3
"""
Ticket Triage Tool — v1

Architecture (locked in design doc):
- Two source files: tickets.csv + bandwidth.csv, joined on current_owner/team_member
- ONE combined Claude API call per ticket, reasoning across:
    description (urgency) + current_status/sprint_id (sprint board comparison)
    + joined bandwidth row (bandwidth retrieval)
- Deterministic context checklist computed in code, NOT inferred by AI
  (VIP account, blocking dependency, SLA clock) — feeds the prompt as ground truth
- Vagueness check runs BEFORE the AI call; flags low-confidence input
- Output: terminal suggestion + one-line reasoning (Recommend, not Act —
  nothing is written back to tickets.csv automatically)
- Full reasoning trace is logged separately to reasoning_log.jsonl,
  file permissions restricted (chmod 600) since ticket descriptions may
  contain sensitive account/customer data
"""

import csv
import json
import os
import stat
import sys
from datetime import datetime, date

try:
    import anthropic
except ImportError:
    print("Missing dependency. Run: pip install anthropic --break-system-packages")
    sys.exit(1)

TICKETS_FILE = "tickets.csv"
BANDWIDTH_FILE = "bandwidth.csv"
LOG_FILE = "reasoning_log.jsonl"
MODEL = "claude-sonnet-4-6"

VAGUE_MIN_WORDS = 6
VAGUE_PHRASES = {"broken", "not working", "issue", "problem", "again", "help"}


# ---------------------------------------------------------------------------
# Deterministic layer (the ~70%: parsing, checklist, vagueness, logging)
# ---------------------------------------------------------------------------

def load_tickets(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load_bandwidth(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return {row["team_member"]: row for row in rows}


def days_until(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        return (d - date.today()).days
    except ValueError:
        return None


def build_context_checklist(ticket):
    """Deterministic checklist flags — sourced from metadata, never inferred by AI.
    This is the direct product-layer response to Failure Mode #2 (missing context)."""
    flags = []

    if ticket.get("is_vip_account", "").strip().lower() == "true":
        flags.append("VIP_ACCOUNT")

    blocks = ticket.get("blocks_ticket_ids", "").strip()
    if blocks:
        blocked = [b for b in blocks.split(";") if b]
        flags.append(f"BLOCKS_{len(blocked)}_TICKET(S): {', '.join(blocked)}")

    sla = ticket.get("sla_deadline", "").strip()
    if sla:
        dleft = days_until(sla)
        if dleft is not None:
            flags.append(f"SLA_DEADLINE_IN_{dleft}_DAY(S)" if dleft >= 0 else "SLA_BREACHED")

    return flags


def is_vague(description):
    """Response to Failure Mode #4 — flags sparse input before the AI ever sees it."""
    desc = description.strip().lower()
    word_count = len(desc.split())
    generic_hit = any(p in desc for p in VAGUE_PHRASES)
    return word_count < VAGUE_MIN_WORDS or (generic_hit and word_count < 10)


def get_bandwidth_row(owner, bandwidth_map):
    row = bandwidth_map.get(owner)
    if not row:
        return None, "no bandwidth record found"
    ts = row.get("last_updated", "unknown")
    return row, f"as of {ts}"


def init_log_file():
    """Create the reasoning-trace log with restricted permissions.
    Sensitive-data flag from the design doc: this log can contain ticket
    description text, so it needs the same access controls as ticket data."""
    if not os.path.exists(LOG_FILE):
        open(LOG_FILE, "a").close()
    os.chmod(LOG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600: owner read/write only


def log_reasoning(entry):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# AI layer (the ~30%: one combined call per ticket)
# ---------------------------------------------------------------------------

def build_prompt(ticket, checklist_flags, bandwidth_row, bandwidth_note, vague):
    bandwidth_summary = (
        f"Owner '{ticket['current_owner']}' currently has "
        f"{bandwidth_row['current_ticket_count']}/{bandwidth_row['capacity']} capacity "
        f"({bandwidth_note})."
        if bandwidth_row else
        f"No current bandwidth data available for owner '{ticket['current_owner']}'."
    )

    return f"""You are a triage assistant helping a PM prioritize sprint tickets.
This is a RECOMMENDATION ONLY — a human will review and confirm before anything changes.
Reason jointly across urgency, sprint state, and team bandwidth. Do not invent facts
not present below.

TICKET
ID: {ticket['ticket_id']}
Description: {ticket['description']}
Current status: {ticket['current_status']}
Sprint: {ticket['sprint_id']}

DETERMINISTIC CONTEXT FLAGS (verified, not your judgment):
{chr(10).join('- ' + f for f in checklist_flags) if checklist_flags else '- none'}

BANDWIDTH
{bandwidth_summary}

INPUT QUALITY FLAG
{'This ticket description is VAGUE/SPARSE — flag your confidence as LOW.' if vague else 'Description quality is sufficient for normal confidence.'}

Respond in exactly this format:
PRIORITY: <P0 | P1 | P2 | P3>
CONFIDENCE: <HIGH | LOW>
REASONING: <one sentence, referencing the specific ticket/context/bandwidth facts above>
"""


def call_model(client, prompt):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def parse_model_output(text):
    result = {"priority": "UNKNOWN", "confidence": "UNKNOWN", "reasoning": text.strip()}
    for line in text.splitlines():
        if line.upper().startswith("PRIORITY:"):
            result["priority"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("CONFIDENCE:"):
            result["confidence"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("REASONING:"):
            result["reasoning"] = line.split(":", 1)[1].strip()
    return result


def get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY in your environment before running.")
    return anthropic.Anthropic(api_key=api_key)


def process_ticket(client, ticket, bandwidth_map, log=True):
    """Shared pipeline: deterministic checklist + vagueness check + one AI call.
    Used by both the CLI (main()) and the Flask UI (app.py) so the two
    surfaces can never silently drift apart in logic."""
    checklist_flags = build_context_checklist(ticket)
    vague = is_vague(ticket["description"])
    bandwidth_row, bandwidth_note = get_bandwidth_row(ticket["current_owner"], bandwidth_map)

    prompt = build_prompt(ticket, checklist_flags, bandwidth_row, bandwidth_note, vague)

    try:
        raw_output = call_model(client, prompt)
    except anthropic.APIError as e:
        return {
            "ticket_id": ticket["ticket_id"],
            "error": str(e),
            "checklist_flags": checklist_flags,
            "vague_input": vague,
            "bandwidth_note": bandwidth_note,
        }

    parsed = parse_model_output(raw_output)
    if vague:
        parsed["confidence"] = "LOW"  # deterministic override, not just a hint to the model

    result = {
        "ticket_id": ticket["ticket_id"],
        "description": ticket["description"],
        "checklist_flags": checklist_flags,
        "vague_input": vague,
        "bandwidth_note": bandwidth_note,
        "priority": parsed["priority"],
        "confidence": parsed["confidence"],
        "reasoning": parsed["reasoning"],
        "raw_model_output": raw_output.strip(),
    }

    if log:
        log_reasoning({"timestamp": datetime.now().isoformat(), **result})

    return result


# ---------------------------------------------------------------------------
# Main (CLI)
# ---------------------------------------------------------------------------

def main():
    try:
        client = get_client()
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    tickets = load_tickets(TICKETS_FILE)
    bandwidth_map = load_bandwidth(BANDWIDTH_FILE)
    init_log_file()

    print(f"Ticket Triage Tool — v1 (Recommend-only, {len(tickets)} tickets loaded)\n")
    print("=" * 78)

    for ticket in tickets:
        result = process_ticket(client, ticket, bandwidth_map)

        if "error" in result:
            print(f"[{result['ticket_id']}] API error — skipping: {result['error']}\n")
            continue

        print(f"[{result['ticket_id']}] {result['description'][:70]}")
        print(f"  Suggested priority: {result['priority']}  (confidence: {result['confidence']})")
        print(f"  Reasoning: {result['reasoning']}")
        if result["checklist_flags"]:
            print(f"  Context flags: {', '.join(result['checklist_flags'])}")
        print(f"  >> Human review required before this is acted on. "
              f"Edit 'human_confirmed_priority' in {TICKETS_FILE} manually.")
        print("-" * 78)

    print(f"\nDone. Full reasoning trace logged to {LOG_FILE} (permissions restricted to owner).")
    print(f"To confirm priorities, manually edit 'human_confirmed_priority' in {TICKETS_FILE}.")


if __name__ == "__main__":
    main()
