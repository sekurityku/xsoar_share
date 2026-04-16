# XSOAR Script Development Helper

Quick-reference guide for writing, reviewing, and validating XSOAR automation scripts.
Derived from `common server python.txt`, `demisto class.txt`, `python code conventions.txt`,
and production scripts: `forensic_triage_script.py`, `browser_extension_collector.py`, `integration_health_check.py`.

---

## 1. Required Boilerplate

```python
import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *

def main():
    try:
        args = demisto.args()
        # ... script logic ...
        return_results(CommandResults(...))
    except Exception as e:
        demisto.error(f"Script failed: {traceback.format_exc()}")
        return_error(f"Script encountered an error: {str(e)}")

if __name__ in ("__main__", "__builtin__", "builtins"):
    main()
```

**Rules:**
- All argument parsing inside `main()` — never at module level (uncaught errors bypass `return_error`)
- `return_error()` calls `sys.exit()` — code after it is unreachable
- Entry guard must use the 3-tuple check

---

## 2. Executing Commands & Parsing Results

### The Correct Pattern

```python
res = demisto.executeCommand("command-name", {"arg1": "value1"})

# 1. ALWAYS check for errors FIRST (before accessing Contents)
if is_error(res):
    raise DemistoException(f"Command failed: {get_error(res)}")

# 2. THEN safely extract contents
contents = res[0].get("Contents", {})

# 3. Handle Contents being a string (some commands return raw JSON)
if isinstance(contents, str):
    try:
        contents = json.loads(contents)
    except (json.JSONDecodeError, TypeError):
        contents = {}
```

### Common Mistakes

| Mistake | Fix |
|---------|-----|
| `is_error(res[0])` — checking single entry | `is_error(res)` — pass the full list |
| `res[0]["Contents"]` — direct dict access | `res[0].get("Contents", {})` — use `.get()` |
| Data extraction BEFORE error check | Error check FIRST, then extract |
| `isError()` — legacy camelCase | `is_error()` — modern snake_case |
| Bare `except: pass` on executeCommand | Log with `demisto.error()`, then handle |

### SearchIncidentsV2 Response Navigation

SearchIncidentsV2 has a nested response structure. Safe navigation:

```python
res = demisto.executeCommand("SearchIncidentsV2", {"query": query, "size": 1})
if is_error(res):
    raise DemistoException(f"Search failed: {get_error(res)}")

# Response is nested: res[0]["Contents"][0]["Contents"]["data"]
# Safe version:
contents = res[0].get("Contents", {})
incidents = None
if isinstance(contents, list) and contents:
    inner = contents[0].get("Contents", {})
    if isinstance(inner, dict):
        incidents = inner.get("data")
elif isinstance(contents, dict):
    incidents = contents.get("data")
```

### XSOAR List Operations

```python
# Read
res = demisto.executeCommand("getList", {"listName": "MyList"})
raw = res[0].get("Contents", "")
# IMPORTANT: "Item not found (8)" is the not-found sentinel, not an error entry
if not raw or raw == "Item not found (8)":
    # List doesn't exist

# Write
demisto.executeCommand("setList", {"listName": "MyList", "listData": json.dumps(data)})
```

### Integration Command Response Structures

Integration commands return `CommandResults` with `outputs`, `readable_output`, and `raw_response`. The `Contents` field in `res[0]` depends on how the integration YAML is configured:

**`outputs_prefix` wraps Contents under a key:**

When an integration's `CommandResults` uses `outputs_prefix`, the `Contents` dict is wrapped under that prefix. This is the most common source of "empty response" bugs when consuming integration commands from scripts.

```python
# Integration code:
return CommandResults(outputs_prefix="Zoom", outputs={"ChatMessage": {"messages": data}})
# Contents in the calling script = {"Zoom": {"ChatMessage": {"messages": [...]}}}

# Integration code:
return CommandResults(outputs_prefix="Zoom.Channel", outputs={**raw_data})
# Contents may be = {"Zoom.Channel": {channel_data...}} or {"Zoom": {"Channel": {...}}}
```

**When integration YAML does NOT have `outputs` defined:**
```python
# Contents = raw_response (the raw API response)
contents = res[0].get('Contents', {})
# e.g. {"result": "...", "task_id": "..."} for llm-gateway without YAML outputs
```

**Safe pattern — try multiple paths (prefix-wrapped, unwrapped, raw):**
```python
contents = res[0].get('Contents', {})
if isinstance(contents, str):
    try:
        contents = json.loads(contents)
    except (json.JSONDecodeError, TypeError):
        contents = {}

if isinstance(contents, dict):
    # Path 1: prefix-wrapped — {"Prefix": {"key": data}}
    wrapped = contents.get('OutputPrefix', {})
    if isinstance(wrapped, dict) and 'key' in wrapped:
        result = wrapped['key']
    # Path 2: unwrapped — {"key": data}
    elif 'key' in contents:
        result = contents['key']
    # Path 3: raw_response — {raw API fields}
    else:
        result = contents.get('raw_field', '')
```

### getEntries Response Structure

`getEntries` returns a nested structure — `res[0].get('Contents')` is an **inner list** of note dicts, not individual entries at the top level:

```python
res = demisto.executeCommand('getEntries', {'filter': {'tags': ['note']}})
if is_error(res):
    raise DemistoException(f'getEntries failed: {get_error(res)}')

entries = res[0].get('Contents', [])
if isinstance(entries, list):
    for note in entries:
        if not isinstance(note, dict):
            continue
        note_text = note.get('Contents', '')  # The actual note content
        user = note.get('Metadata', {}).get('user', '')
```

---

## 3. Returning Results

### Preferred: CommandResults

```python
return_results(CommandResults(
    outputs_prefix="MyScript.Result",    # Context path (required for context output)
    outputs_key_field="id",              # Primary key for dedup in context
    outputs=output_data,                 # Dict or list
    readable_output=tableToMarkdown("Results", output_data),
    raw_response=raw_api_response,
))
```

### War Room Note (no context)

```python
return_results({
    "Type": entryTypes["note"],
    "ContentsFormat": formats["markdown"],
    "Contents": markdown_report,
    "HumanReadable": markdown_report,
})
```

### File Attachment

```python
return_results(fileResult("report.csv", csv_content))
```

### return_results vs demisto.results

| Method | Use When |
|--------|----------|
| `return_results(CommandResults(...))` | Standard — always preferred |
| `return_results(str)` | Simple text output |
| `demisto.results(msg)` | Mid-script progress messages to War Room (not final output) |
| `return_error(msg)` | Fatal error — terminates script via `sys.exit()` |
| `return_warning(msg)` | Non-fatal warning — does NOT exit |

---

## 4. Error Handling

### Function-Level Pattern (raise, don't return_error)

```python
def do_work(client, args):
    res = demisto.executeCommand("some-command", args)
    if is_error(res):
        raise DemistoException(f"Failed: {get_error(res)}")
    return res[0].get("Contents", {})
```

### main() Catches Everything

```python
def main():
    try:
        result = do_work(args)
        return_results(CommandResults(...))
    except Exception as e:
        demisto.error(f"Script failed: {traceback.format_exc()}")
        return_error(f"Failed to execute script: {str(e)}")
```

### Non-Fatal Operations (notifications, cleanup)

```python
try:
    demisto.executeCommand("send-mail", mail_args)
except Exception as e:
    demisto.debug(f"Email notification failed (non-fatal): {str(e)}")
```

### War Room Readable Errors Are Important

When a non-fatal operation fails (e.g., Jira ticket creation, notification send), always surface the failure in the `readable_output` / human-readable report — not just in server logs. Analysts and engineers reviewing results in the War Room or on the XSOAR incident won't check `demisto.error()` logs unless they already know something went wrong. If a failure is silent in the War Room, it's effectively invisible.

```python
# Bad — failure only visible in server logs
if not ticket_key:
    demisto.error("Jira ticket creation failed")

# Good — failure visible in War Room output
if not ticket_key:
    demisto.error("Jira ticket creation failed")
    human_readable += "\n**Jira ticket:** FAILED — check script logs for details"
```

This applies to any script action whose success or failure matters to the user: ticket creation, notification delivery, external API calls, incident creation, etc. The `readable_output` is the primary feedback channel.

### Custom Exceptions

For scripts with distinct failure modes:

```python
class HostOfflineError(Exception):
    pass

class DeviceNotFoundError(Exception):
    pass
```

---

## 5. Logging

| Method | Level | Destination | Use |
|--------|-------|-------------|-----|
| `demisto.debug(msg)` | DEBUG | Server logs | Detailed data, API responses, variable values |
| `demisto.info(msg)` | INFO | Server logs | Major workflow steps, human-visible status |
| `demisto.error(msg)` | ERROR | Server logs | Failures, exceptions |
| `demisto.log(msg)` | — | War Room log | Deprecated — avoid |
| `LOG(msg)` | — | — | Deprecated — use `demisto.debug()` |

---

## 6. Argument Handling

### Safe Argument Parsing

```python
args = demisto.args()

# Strings
name = args.get("name", "default_value")

# Booleans — use argToBoolean (handles "true", "false", "yes", "no", True, False)
verbose = argToBoolean(args.get("verbose", "false"))

# Numbers — use arg_to_number (returns None on failure, doesn't throw)
limit = arg_to_number(args.get("limit", 100)) or 100

# Lists — use argToList (handles comma-separated strings and actual lists)
items = argToList(args.get("items", ""))

# Dates — use arg_to_datetime (handles ISO, relative "3 days ago", epoch)
start_date = arg_to_datetime(args.get("start_date"))
```

**Never use `int()` or `float()` directly on args** — they throw uncaught ValueError on bad input.

---

## 7. Integration Instance Selection

When a script needs to call a specific integration instance:

```python
cmd_args = {"arg1": "value1"}
if instance_name:
    cmd_args["using"] = instance_name

res = demisto.executeCommand("command-name", cmd_args)
```

---

## 8. Internal API Calls (XSOAR 8 SaaS)

### Option A: internalHttpRequest (no integration dependency)

```python
try:
    response = demisto.internalHttpRequest(
        method="POST",
        uri="/settings/integration/search",
        body=json.dumps({})
    )
    if response and response.get("statusCode") == 200:
        data = json.loads(response.get("body", "{}"))
except Exception:
    pass  # Fallback to core-api-post
```

**Note:** `internalHttpRequest` has limited (read-only) permissions in playbook context.

### Option B: Core REST API (requires integration configured)

```python
res = demisto.executeCommand("core-api-post", {
    "uri": "/settings/integration/search",
    "body": {}
})
if res and not is_error(res):
    contents = res[0].get("Contents", {})
    # Handle nested "response" key
    if isinstance(contents, dict) and "response" in contents:
        data = contents["response"]
```

### Recommended: Try both with fallback

```python
def make_api_call(method, uri, body=None):
    # Try internalHttpRequest first
    try:
        response = demisto.internalHttpRequest(
            method=method, uri=uri,
            body=json.dumps(body) if body else None
        )
        if response and response.get("statusCode") == 200:
            return json.loads(response.get("body", "{}"))
    except Exception:
        pass

    # Fallback to Core REST API
    cmd = "core-api-get" if method.upper() == "GET" else "core-api-post"
    cmd_args = {"uri": uri}
    if body:
        cmd_args["body"] = body

    res = demisto.executeCommand(cmd, cmd_args)
    if res and not is_error(res):
        content = res[0].get("Contents", {})
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return {}
        if isinstance(content, dict) and "response" in content:
            return content["response"]
        return content if isinstance(content, dict) else {}
    return {}
```

---

## 9. Incident Deduplication Pattern

Used by `integration_health_check.py` and `XSOARHealthCheckAndJira.py`:

```python
import hashlib

def generate_issue_hash(signature):
    """Create stable hash from error signature."""
    return hashlib.md5(signature.encode()).hexdigest()

def check_existing_incident(issue_hash, incident_type):
    """Search for active XSOAR incident with matching hash."""
    query = f'-category:job type:"{incident_type}" and healthissuehash:"{issue_hash}"'
    res = demisto.executeCommand("SearchIncidentsV2", {"query": query, "size": 1})
    if is_error(res):
        return False
    # ... safely navigate response (see Section 2) ...
    return bool(matching_incidents)

# Usage: hash the error signature, check before creating
issue_hash = generate_issue_hash(f"{brand}_{instance}_{error_msg}")
if not check_existing_incident(issue_hash, "XSOAR Support"):
    demisto.executeCommand("createNewIncident", {
        "type": "XSOAR Support",
        "name": incident_name,
        "healthissuehash": issue_hash,
        # ... other fields ...
    })
```

---

## 10. Conventions Quick Reference

### Naming
- Variables/functions: `snake_case`
- Constants: `UPPER_SNAKE_CASE` (but define inside `main()` if derived from args)
- Command functions (integrations): `verb_noun_command` suffix

### Incident Field Names
- XSOAR stores custom field IDs as **lowercase with no spaces or special characters**
- Example: a field labeled "IR Escalation Notes" in the UI → `irescalationnotes` in code
- Always use the XSOAR field ID (not the display label) in `custom_fields.get()` and `setIncident` calls
- Native fields (`name`, `severity`, `status`, `owner`, `occurred`, `type`) are accessed directly on the incident dict, not under `CustomFields`

### Dates
- Never use epoch for user-facing output
- Use `%Y-%m-%dT%H:%M:%SZ` for ISO format
- Use `timestamp_to_datestring()` to convert epoch
- Use `arg_to_datetime()` to parse user input

### Entry Types
```python
EntryType.NOTE      # 1 — standard note
EntryType.ERROR     # 4 — error entry
EntryType.FILE      # 3 — file attachment
EntryType.WARNING   # 11 — warning
```

### Severity Values
```python
IncidentSeverity.LOW        # 1
IncidentSeverity.MEDIUM     # 2
IncidentSeverity.HIGH       # 3
IncidentSeverity.CRITICAL   # 4
```

---

## 11. Validation Checklist

Use this before deploying any script:

- [ ] Imports: `demistomock`, `CommonServerPython`, `CommonServerUserPython`
- [ ] Entry guard: `if __name__ in ("__main__", "__builtin__", "builtins")`
- [ ] All arg parsing inside `main()` try/except
- [ ] `is_error()` (not `isError()`) used consistently
- [ ] `is_error(res)` checks full result list, not `res[0]`
- [ ] Error check BEFORE contents extraction
- [ ] `.get()` used for all Contents access (not direct `[]` access)
- [ ] Contents type checked (`isinstance(contents, str)` → `json.loads()`)
- [ ] `return_error()` used only in `main()` catch block (not in helper functions)
- [ ] Helper functions raise `DemistoException`, not call `return_error()`
- [ ] `traceback` imported if `traceback.format_exc()` is used
- [ ] `CommandResults` uses `outputs_prefix` and `outputs_key_field`
- [ ] `arg_to_number()` used instead of `int()` for numeric args
- [ ] No mid-function imports
- [ ] No unused variables
- [ ] `demisto.incident()` caveat understood (stale data from script start)
- [ ] `createNewIncident` result checked with `is_error()`
- [ ] Non-fatal operations wrapped in try/except with `demisto.debug()`

---

## 12. Dynamic Section Scripts (Layout Display Widgets)

Dynamic section scripts render HTML/CSS on the layout. They run server-side on page load — no auto-refresh, no client-side scripting.

### Boilerplate

```python
from CommonServerPython import *
import json

COLORS = {
    'bg_primary': '#0e0e0e',
    'bg_secondary': '#1a1a1a',
    'bg_tertiary': '#242424',
    'text_primary': '#e0e0e0',
    'text_secondary': '#a0a0a0',
    'text_tertiary': '#707070',
    'border': '#2a2a2a',
}

def clean_html(s):
    """Remove newline/extra whitespace — prevents spacing gaps in XSOAR 8."""
    return ' '.join(s.split())

def main():
    try:
        incident = demisto.incident()
        custom_fields = incident.get('CustomFields', {})

        html = clean_html(f"""
        <div style="background:{COLORS['bg_secondary']};padding:12px;border-radius:4px;">
            <span style="color:{COLORS['text_primary']};">Content here</span>
        </div>
        """)

        return_results({
            'Type': entryTypes['note'],
            'ContentsFormat': formats['html'],
            'Contents': html
        })
    except Exception as e:
        return_error(f'Display script failed: {str(e)}')

if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
```

### Key Rules

- **Always use `clean_html()`** — XSOAR 8's React sanitizer adds whitespace gaps for newlines in HTML
- **All CSS must be inline** — no `<style>` blocks, no external stylesheets
- **Return type must be `entryTypes['note']` with `formats['html']`** — this is what makes it render on the layout
- **`demisto.incident()` is stale** — captures state at script start, does not reflect changes made after the script begins executing
- **No auto-refresh** — user must reload the page to see updated data
- **`<a href>` tags work** — anchor links render and are clickable in XSOAR 8 dynamic sections (`target="_blank"` for external links)
- **`<img>` tags are stripped** — XSOAR's HTML sanitizer removes image tags; use `fileResult()` / entry type 3 for images instead
- **Markdown buttons (`%%%{...}%%%`) only work in markdown format** — they do NOT work inside `formats['html']` entries; for actionable buttons in HTML sections, use XSOAR layout buttons (Section 14) instead

### HTML Escaping

Always escape external/user-provided data before embedding in HTML. This includes:
- LLM output, API response text, chat messages, field values
- URL values placed in `href="..."` attributes (escape `&` and `"`)

```python
def escape_html(text):
    """Escape HTML special chars. Use on ALL external data in dynamic sections."""
    return (text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
```

**Common mistakes:**
- Escaping the display text but not the `href` URL value (`&` in query params breaks HTML)
- Displaying LLM output in `white-space:pre-wrap` without escaping (XSS risk)
- Using a fallback value without escaping: `escape_html(name) or channel_id` — the fallback `channel_id` also needs escaping

### Reading Incident Data

```python
incident = demisto.incident()
custom_fields = incident.get('CustomFields', {})

# Standard fields
case_name = incident.get('name', '')
severity = incident.get('severity', 0)
status = incident.get('status', 0)
owner = incident.get('owner', '')
occurred = incident.get('occurred', '')

# Custom fields
notes = custom_fields.get('escalation_notes', '')
channel_id = custom_fields.get('ir_channel_id', '')

# Grid fields (may be JSON string, list, or dict)
grid_raw = custom_fields.get('important_links', [])
if isinstance(grid_raw, str):
    try:
        grid_data = json.loads(grid_raw)
    except (json.JSONDecodeError, TypeError):
        grid_data = []
elif isinstance(grid_raw, list):
    grid_data = grid_raw
elif isinstance(grid_raw, dict):
    grid_data = [grid_raw]
```

### Common Patterns

**Colored badge:**
```python
def get_badge(label, color):
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;'
        f'border-radius:3px;font-size:10px;font-weight:600;'
        f'text-transform:uppercase;">{label}</span>'
    )
```

**Section with header:**
```python
html = clean_html(f"""
<div style="background:{COLORS['bg_secondary']};border:1px solid {COLORS['border']};
    border-radius:4px;overflow:hidden;">
    <div style="background:{COLORS['bg_tertiary']};padding:8px 12px;
        border-bottom:1px solid {COLORS['border']};">
        <span style="color:{COLORS['text_primary']};font-size:12px;font-weight:600;">
            Section Title
        </span>
    </div>
    <div style="padding:12px;">
        <!-- content -->
    </div>
</div>
""")
```

**Gradient banner:**
```python
html = clean_html(f"""
<div style="background:linear-gradient(135deg,{accent_color},#000);
    padding:12px 16px;border-radius:6px;border-left:6px solid {accent_color};color:#fff;">
    <div style="font-size:18px;font-weight:700;">{title}</div>
    <div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
        {info_line}
    </div>
</div>
""")
```

**Flex grid for metadata:**
```python
style = "display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;"
```

**Empty state placeholder:**
```python
html = clean_html(f"""
<div style="background:{COLORS['bg_secondary']};border:1px solid {COLORS['border']};
    padding:16px;text-align:center;border-radius:4px;">
    <p style="color:{COLORS['text_tertiary']};font-size:11px;margin:0;">
        Data not yet populated. Run the enrichment playbook.
    </p>
</div>
""")
```

### Color Conventions

| Use Case | Color | Notes |
|----------|-------|-------|
| CrowdStrike layouts | `#d31912` (red) | Existing CrowdStrike accent |
| IR layouts | `#2196f3` (blue) | Visually distinct from CrowdStrike |
| Dark backgrounds | `#0e0e0e` / `#1a1a1a` / `#242424` | Primary / secondary / tertiary |
| Text | `#e0e0e0` / `#a0a0a0` / `#707070` | Primary / secondary / tertiary |
| Critical severity | `#d31912` | |
| High severity | `#ff6b35` | |
| Medium severity | `#ffa500` | |
| Low severity | `#4a9aea` or `#4a4a4a` | |

### Reference Scripts

- `automation-CrowdStrikeCaseMetadataDisplay.py` — gradient banner, badges, info line with separators
- `automation-CrowdStrikeCaseAlertsDisplay.py` — card-based layout, grid fields, severity/status mapping
- `automation-CrowdStrikeCaseAnalysisResultsDisplay.py` — entity-type grouping, entity-colored sections
- `automation-IRescalationInfoDisplay.py` — IR Tab 1, blue accent theme, LLM-generated content, link cards
- `automation-IRTriageCriteriaDisplay.py` — XSOAR list-driven criteria display, placeholder decision buttons
- `automation-IRChatHubDisplay.py` — multi-section display (channel header with activity stats, meeting links, AI summary, IOC extraction, members with role badges, threaded messages with file attachments)

---

## 13. Markdown Buttons (Execute Commands from Layout)

XSOAR supports interactive buttons in markdown output that execute integration commands when clicked. Useful for action buttons in War Room entries or layout widgets.

### Button Syntax

```python
button = (
    f'{{{{{{{{{background:green}}}}}}}}}'  # Background color
    f'({{{{{{{{{color:white}}}}}}}}}'       # Text color
    f'(%%%{{"message": "Button Label", '
    f'"action": "command-name", '
    f'"params": {{"param1": "value1", "param2": "value2"}}}}'
    f'%%%))'
)
```

### Practical Example (f-string with escaped braces)

```python
# Green "Approve" button that calls a command
approve_btn = (
    f'{{{{background:green}}}}({{{{color:white}}}}'
    f'(%%%{{"message": "Approve", '
    f'"action": "my-approve-command", '
    f'"params": {{"item_id": "{item_id}", "status": "approved"}}}}'
    f'%%%))'
)

# Red "Reject" button
reject_btn = (
    f'{{{{background:red}}}}({{{{color:white}}}}'
    f'(%%%{{"message": "Reject", '
    f'"action": "my-reject-command", '
    f'"params": {{"item_id": "{item_id}", "status": "rejected"}}}}'
    f'%%%))'
)

# Include in readable_output
human_readable = f'**Action Required:**\n{approve_btn}\n{reject_btn}'
```

### Key Rules

- **Quad braces `{{{{` and `}}}}`** — Python f-string escaping for XSOAR's `{{` markdown syntax
- **`%%%{...}%%%`** wraps the JSON action payload
- **`message`** — button label text visible to the user
- **`action`** — the integration command name to execute on click
- **`params`** — dict of arguments passed to the command
- **Works in `readable_output`** of `CommandResults` or War Room markdown entries
- Buttons execute the command and display the result inline

### Reference

- `expel.py` integration — `expel-set-activity-authorization` and `expel-update-remediation-status` button patterns

---

## 14. XSOAR Layout Buttons (Script Execution)

XSOAR layouts support buttons that execute automation scripts (not playbooks) on click. These are configured in the layout JSON, not in code. The button triggers a script that can read/write incident fields, call integration commands, and return results.

### How It Works

1. Layout JSON defines a button widget pointing to an automation script
2. User clicks the button on the layout
3. XSOAR executes the script in the context of the current incident
4. Script can read `demisto.incident()`, call `demisto.executeCommand()`, and write back to fields

### Common Button Script Pattern

```python
def main():
    try:
        incident = demisto.incident()
        custom_fields = incident.get('CustomFields', {})
        channel_id = custom_fields.get('ir_channel_id', '')

        if not channel_id:
            return_error('No IR channel ID set on this incident.')

        # Execute the integration command
        res = demisto.executeCommand('zoom-invite-to-channel', {
            'channel_id': channel_id,
            'member': demisto.args().get('user_email', ''),
        })

        if is_error(res):
            return_error(f'Failed to invite: {get_error(res)}')

        return_results(CommandResults(
            readable_output=f'Invited {demisto.args().get("user_email")} to the IR channel.'
        ))
    except Exception as e:
        return_error(f'Button script failed: {str(e)}')
```

### Key Points

- **Scripts, not playbooks** — buttons execute scripts for speed (no playbook engine overhead)
- Script runs in the incident context — `demisto.incident()` returns the current incident
- Script arguments come from `demisto.args()` — configured in the layout button definition
- Script can call any integration command via `demisto.executeCommand()`
- Keep scripts fast — they block the UI until they return

### Use Cases for IR Layout

| Button | Script Action |
|--------|--------------|
| Add Stakeholder | `zoom-invite-to-channel` with user email |
| Remove Stakeholder | `zoom-remove-from-channel` |
| Create Document | `google-docs-create-document` + append to grid field |
| Send Update Now | Compile summary fields + `zoom-send-message` |
| Add Tracker Item | Append row to incident tracker grid field |

---

## 15. Workplan & Task Data Access

Two different API endpoints exist for accessing playbook task data. They return **different structures**.

### Option A: Full Workplan (Hierarchical) — RECOMMENDED

Returns the complete playbook tree with task details, playbook names, and sub-playbook nesting.
Use `core-api-get` with `/investigation/{incident_id}/workplan`.

```python
core_instance = demisto.args().get("core_rest_api_instance_name")

res = demisto.executeCommand("core-api-get", {
    "uri": f"/investigation/{incident_id}/workplan",
    "using": core_instance
})
if is_error(res):
    raise DemistoException(f"Failed to get workplan: {get_error(res)}")

response = res[0].get('Contents', {}).get('response', {})

inv_playbook = response.get("invPlaybook", {})
playbook_name = inv_playbook.get("name", "Unknown Playbook")
playbook_id = inv_playbook.get("playbookId", "Unknown")

# Tasks are a dict keyed by task ID (not a list)
tasks = inv_playbook.get("tasks", {})
```

**Task structure (each entry in the tasks dict):**

```python
for task_id, task_data in tasks.items():
    task_details = task_data.get("task", {})  # Inner task details object

    task_name = task_details.get("name", "Unknown Task")
    task_state = task_data.get("state", "")        # "Completed", "Error", etc.
    task_type = task_data.get("type", "")           # "regular", "playbook", etc.
    brand = task_details.get("brand", "")
    script_id = task_details.get("scriptId", "")

    start_date = task_data.get("startDate", "")
    completed_date = task_data.get("completedDate", "")
    has_errors = task_data.get("hasErrorEntries", False)

    # Sub-playbook tasks are nested — must traverse recursively
    subplaybook = task_data.get("subPlaybook", {})
    if subplaybook and isinstance(subplaybook, dict):
        sub_tasks = subplaybook.get("tasks", {})
        sub_playbook_name = subplaybook.get("name", "Unknown Subplaybook")
        # Recurse into sub_tasks with parent playbook context
```

**Recursive traversal pattern** (from `XSOAR_CCT_ExportTaskData.py`):

```python
all_tasks = {}

def process_task_dict(tasks_dict, parent_info=None):
    for task_id, task_data in tasks_dict.items():
        if parent_info:
            task_data['parent_playbook_id'] = parent_info.get('playbook_id')
            task_data['parent_playbook_name'] = parent_info.get('playbook_name')

        all_tasks[task_id] = task_data

        subplaybook = task_data.get('subPlaybook', {})
        if subplaybook and isinstance(subplaybook, dict):
            sub_tasks = subplaybook.get('tasks', {})
            if sub_tasks and isinstance(sub_tasks, dict):
                sub_parent = {
                    'playbook_id': subplaybook.get('playbookId', playbook_id),
                    'playbook_name': subplaybook.get('name', 'Unknown Subplaybook')
                }
                process_task_dict(sub_tasks, sub_parent)

process_task_dict(inv_playbook.get('tasks', {}))
```

### Option B: Filtered Task List (Flat) — LIMITED

Returns a flat list of tasks matching state/type filters. Lighter weight but **does not reliably
include playbook names** (`ancestorName` and `playbookName` are often null).

Uses `internalHttpRequest` or `core-api-post` with `/investigation/{id}/workplan/tasks`.

```python
tasks_data = make_api_call("POST", f"investigation/{inc_id}/workplan/tasks", {
    "states": ["Error"],
    "types": ["regular", "condition", "collection", "playbook"],
})
```

**Key limitation:** `task.get("ancestorName")` and `task.get("playbookName")` return `None`
(key present, value null) — Python `.get()` default only applies when key is **missing**, not
when value is `None`. Use `or` chaining:

```python
playbook_name = (
    task.get("ancestorName")
    or task.get("playbookName")
    or "Unknown"
)
```

### When to Use Which

| Endpoint | Use Case | Pros | Cons |
|----------|----------|------|------|
| `/workplan` (Option A) | Full task analysis, metrics, reporting | Complete data, playbook hierarchy | Heavier response, requires recursive traversal |
| `/workplan/tasks` (Option B) | Quick state-filtered checks | Lightweight, server-side filtering | Missing playbook names, flat structure |

### Reference

- `XSOAR_CCT_ExportTaskData.py` — full workplan traversal with sub-playbook recursion

---

## 16. XSOAR 8 SaaS Considerations

- **No Docker/CPU/disk/worker access** — Palo manages infrastructure
- **Log bundle collection unavailable** — on-prem only
- **`internalHttpRequest` may have restricted permissions** in playbook context
- **`core-api-post` is the standard** for internal API calls in SaaS
- **Multi-tenant**: configure Core REST API at parent level, use `"Use tenant"` parameter
- **`demisto.executeCommand()` is script-only** — integrations use `BaseClient._http_request()`

---

## 17. Useful demisto Class Methods (Scripts)

| Method | Returns | Notes |
|--------|---------|-------|
| `demisto.args()` | Dict of script arguments | |
| `demisto.executeCommand(cmd, args)` | List of entry dicts | Script-only |
| `demisto.incident()` | Current incident dict | Stale — captures state at script start |
| `demisto.context()` | Full context data | |
| `demisto.investigation()` | Investigation ID info | |
| `demisto.demistoUrls()` | Server URLs dict | |
| `demisto.internalHttpRequest(method, uri, body)` | `{statusCode, body, headers}` | |
| `demisto.getModules()` | All integration instances | |
| `demisto.setContext(path, value)` | None | Script-only |
| `demisto.dt(obj, transform)` | Extracted field | DT language |
| `demisto.get(obj, field, default)` | Field value | Dot-notation |
| `demisto.getFilePath(entry_id)` | `{id, path, name}` | |
| `demisto.uniqueFile()` | UUID filename | |

---

## 18. Useful CommonServerPython Functions

| Function | Purpose |
|----------|---------|
| `is_error(res)` | Check if executeCommand result is error |
| `get_error(res)` | Extract error message |
| `argToBoolean(val)` | Parse bool from string/bool |
| `argToList(val)` | Parse list from comma-separated string |
| `arg_to_number(val)` | Parse int (returns None on failure) |
| `arg_to_datetime(val)` | Parse datetime from various formats |
| `tableToMarkdown(name, data)` | Convert data to markdown table |
| `timestamp_to_datestring(ts)` | Epoch to human-readable date |
| `fileResult(filename, data)` | Create file war room entry |
| `execute_command(cmd, args)` | Higher-level wrapper with auto error check |
| `safe_load_json(val)` | Parse JSON safely |
| `assign_params(**kwargs)` | Build dict excluding None values |
| `batch(iterable, size)` | Chunk iterable into batches |
| `dict_safe_get(d, keys, default)` | Safe nested dict access |
