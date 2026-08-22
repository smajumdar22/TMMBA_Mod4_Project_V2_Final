#!/usr/bin/env python3
"""
Ticket Triage Tool — minimal web UI

Same locked architecture as triage.py (the CLI): deterministic checklist,
vagueness check, one combined AI call per ticket, Recommend-only.
This file adds NO new decisions — it's a thin display layer on top of
triage.py's process_ticket(), so CLI and web can never silently diverge
in logic.

Still Recommend-only: this UI does NOT write human_confirmed_priority
back to tickets.csv. That stays a manual edit for v1, per the locked
Cut List (C4) decision — adding write-back here would be a scope change
this session hasn't made, not something to slip in quietly.
"""

from flask import Flask, render_template_string

from triage import (
    load_tickets, load_bandwidth, get_client, process_ticket, init_log_file,
    TICKETS_FILE, BANDWIDTH_FILE,
)

app = Flask(__name__)

PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>Ticket Triage — v1</title>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; color: #1b2a4a; }
    h1 { font-size: 22px; }
    .note { color: #666; font-size: 14px; margin-bottom: 24px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #ddd; vertical-align: top; }
    th { background: #1b2a4a; color: white; font-size: 13px; }
    td { font-size: 14px; }
    .P0 { color: #b00020; font-weight: bold; }
    .P1 { color: #c0562a; font-weight: bold; }
    .P2 { color: #6a6a00; }
    .P3 { color: #2a7a2a; }
    .LOW { background: #fff3cd; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
    .flags { font-size: 12px; color: #555; }
    .err { color: #b00020; }
    .confirm-note { font-size: 12px; color: #888; }
  </style>
</head>
<body>
  <h1>Ticket Triage — v1 (Recommend-only)</h1>
  <p class="note">
    Suggestions only. Nothing here is auto-applied.
    Confirm priorities by manually editing <code>human_confirmed_priority</code> in
    <code>{{ tickets_file }}</code>.
  </p>
  <table>
    <tr>
      <th>Ticket</th><th>Description</th><th>Priority</th><th>Confidence</th>
      <th>Reasoning</th><th>Context flags</th>
    </tr>
    {% for r in results %}
    <tr>
      <td>{{ r.ticket_id }}</td>
      <td>{{ r.description[:80] if r.description else '' }}</td>
      {% if r.error %}
        <td colspan="4" class="err">API error: {{ r.error }}</td>
      {% else %}
        <td class="{{ r.priority }}">{{ r.priority }}</td>
        <td>{% if r.confidence == 'LOW' %}<span class="LOW">LOW</span>{% else %}{{ r.confidence }}{% endif %}</td>
        <td>{{ r.reasoning }}</td>
        <td class="flags">{{ r.checklist_flags | join(', ') if r.checklist_flags else '—' }}</td>
      {% endif %}
    </tr>
    {% endfor %}
  </table>
  <p class="confirm-note">Full reasoning trace logged to reasoning_log.jsonl (access-restricted).</p>
</body>
</html>
"""


@app.route("/")
def index():
    client = get_client()
    tickets = load_tickets(TICKETS_FILE)
    bandwidth_map = load_bandwidth(BANDWIDTH_FILE)
    init_log_file()

    results = [process_ticket(client, t, bandwidth_map) for t in tickets]
    return render_template_string(PAGE, results=results, tickets_file=TICKETS_FILE)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
