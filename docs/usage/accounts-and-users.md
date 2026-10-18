# Accounts & Multi-User Management

Monitor Agent Usage tracks token telemetry across multiple team members, workstations, and authentication types.

---

## Account Discovery & Authentication Types

Every tracked source of token data is represented as an **Account Record**:

| Auth Type | Description | Required Configuration Fields |
|---|---|---|
| `local_fs` | Auto-discovered or manually registered local transcript directory. | `root_path`, optional `cli_path` or `desktop_path` |
| `api_key` | Direct provider usage reporting API (admin / organization key). | `api_key` (Anthropic, OpenAI, or xAI) |
| `session_key` | Direct web conversation REST API sync without proxy. | `session_key`, `org_id` (Claude) or `cookie` (Grok) |
| `demo` | Synthetic, realistic multi-turn session data for demonstration. | `{"demo": true}` |

---

## Auto-Discovered Accounts

When the server starts or sync runs, default accounts are registered automatically for each accessible local user:

- `acct_{username}_antigravity_local`: Scans `~/.gemini/antigravity` and `~/.gemini/antigravity-cli`
- `acct_{username}_claude_local`: Scans `~/.claude/projects` and desktop data
- `acct_{username}_codex_local`: Scans `~/.codex/sessions` and archived sessions
- `acct_{username}_grok_local`: Scans `~/.grok` and `~/.config/grok`

In WSL setups, Windows host accounts are registered with a `_win` suffix (e.g. `acct_{username}_claude_win`).

---

## Adding Custom Users & Directory Paths

Through the web dashboard's **Accounts & Users** modal:

1. Click **Accounts & Users** in the top bar.
2. Under **Register New User**, enter:
   - **Username**: Unique identifier (e.g. `dave`).
   - **Display Name**: Human-friendly label (e.g. `Dave Miller`).
   - **Home Directory Path**: Custom root directory (e.g. `/home/dave` or `/mnt/c/Users/dave`).
3. Click **Create User & Register Accounts**.
4. The system validates directory accessibility, registers accounts for all 4 providers, and immediately runs historical backfill.

---

## Configuring Cloud Usage APIs (Proxy-Free)

If you prefer to sync directly from provider organization usage endpoints without relying on local files:

### 1. Anthropic Admin Usage API (Claude)
- Go to the **Accounts & Users** modal.
- Click **Add Provider Account**.
- Select **Claude** and choose auth type **Admin API Key**.
- Enter your `x-api-key` (requires Anthropic Admin permissions).
- The collector queries `/v1/organizations/usage_report/messages` directly over HTTPS.

### 2. OpenAI Organization Usage API (Codex)
- Select **Codex** and auth type **Admin API Key**.
- Enter your OpenAI Admin API Key.
- The collector queries `/v1/organization/usage/completions` directly over HTTPS.

### 3. xAI API (Grok)
- Select **Grok** and enter your xAI API Key.
- The collector queries `https://api.x.ai/v1/api-key` for prompt and completion token totals.

---

## Sample Data Lifecycle (Demo Mode)

For product demonstrations, client presentations, or testing visualizations without waiting for developer activity:

- Click **Load Sample Data** in the dashboard header (or call `POST /api/demo/seed`).
- This seeds realistic, multi-user conversations for `Alex Rivera` and `Sam Chen` across Grok-3, Codex, Claude Sonnet, and Antigravity Gemini.
- To remove all demo records and leave only real usage, click **Clear Sample Data** (or call `POST /api/demo/clear`).

