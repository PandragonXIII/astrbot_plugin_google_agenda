# astrbot_plugin_google_agenda

AstrBot plugin for creating:

- Google Calendar events
- Google Tasks todo items

It exposes both command wrappers and LLM tools so the model can call tools when the user says things like:

- “明天下午三点和导师开会，提醒我，地点办公室”
- “把周五之前交 project report 加到待办”

## LLM tools

### `create_google_calendar_event`

Creates an event through Google Calendar API.

Important arguments:

- `title` required
- `start` required: ISO datetime/date, e.g. `2026-06-16T15:00:00` or `2026-06-16`
- `end` optional: if missing, default duration is used
- `description`, `location`, `timezone`, `calendar_id` optional

### `create_google_task`

Creates a task through Google Tasks API.

Important arguments:

- `title` required
- `due` optional: RFC3339/ISO date or datetime
- `notes`, `tasklist_id` optional

## Commands

- `/gagenda_status` check config/auth status
- `/gagenda_auth` run OAuth flow and save token
- `/gcal_event {json}` create calendar event for testing
- `/gtask_create {json}` create task for testing

Example:

```text
/gcal_event {"title":"开会","start":"2026-06-16T15:00:00","end":"2026-06-16T16:00:00","location":"办公室"}
/gtask_create {"title":"交 project report","due":"2026-06-19"}
```

## Google setup

1. In Google Cloud Console, create a project.
2. Enable **Google Calendar API** and **Google Tasks API**.
3. Configure OAuth consent screen.
4. Create OAuth Client ID, type **Desktop app**.
5. Download credentials JSON.
6. Set plugin config `credentials_path` to that JSON path.
7. Run `/gagenda_auth` once. It opens/prints an authorization URL and saves `token.json`.

## Notes

- The plugin uses OAuth user credentials, not a service account. This is what you want for syncing with your phone's personal Google Calendar/Tasks.
- `requirements.txt` must be installed in AstrBot's Python environment.
