import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except Exception:  # pragma: no cover - dependency may be absent before install
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]


@register(
    "astrbot_plugin_google_agenda",
    "AA",
    "Create Google Calendar events and Google Tasks from commands or LLM tools",
    "0.1.0",
)
class GoogleAgendaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._auth_lock = asyncio.Lock()

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _plugin_dir(self) -> Path:
        return Path(__file__).resolve().parent

    def _credentials_path(self) -> str:
        return str(self._cfg("credentials_path", "") or "").strip()

    def _token_path(self) -> str:
        configured = str(self._cfg("token_path", "") or "").strip()
        return configured or str(self._plugin_dir() / "token.json")

    def _timezone(self, timezone_name: str | None = None) -> str:
        return (timezone_name or self._cfg("default_timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()

    def _duration_minutes(self) -> int:
        try:
            value = int(self._cfg("default_event_duration_minutes", 60))
            return max(1, value)
        except Exception:
            return 60

    def _deps_ok(self) -> tuple[bool, str]:
        if not all([Request, Credentials, InstalledAppFlow, build]):
            return False, "Google API dependencies are missing. Install requirements.txt in AstrBot's Python environment."
        return True, "ok"

    def _load_credentials_sync(self):
        ok, msg = self._deps_ok()
        if not ok:
            raise RuntimeError(msg)

        credentials_path = self._credentials_path()
        if not credentials_path:
            raise RuntimeError("credentials_path is not configured.")
        if not os.path.exists(credentials_path):
            raise RuntimeError(f"credentials_path does not exist: {credentials_path}")

        token_path = self._token_path()
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_token_sync(creds)

        if not creds or not creds.valid:
            raise RuntimeError("Google token is missing or invalid. Run /gagenda_auth first.")
        return creds

    def _save_token_sync(self, creds) -> None:
        token_path = self._token_path()
        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        try:
            os.chmod(token_path, 0o600)
        except Exception:
            pass

    def _auth_sync(self) -> str:
        ok, msg = self._deps_ok()
        if not ok:
            return msg

        credentials_path = self._credentials_path()
        if not credentials_path:
            return "credentials_path is not configured."
        if not os.path.exists(credentials_path):
            return f"credentials_path does not exist: {credentials_path}"

        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        # console flow works on headless servers: prints URL, accepts pasted code.
        creds = flow.run_console()
        self._save_token_sync(creds)
        return f"Google authorization succeeded. Token saved to {self._token_path()}"

    async def _get_service(self, service_name: str, version: str):
        creds = await asyncio.to_thread(self._load_credentials_sync)
        return await asyncio.to_thread(build, service_name, version, credentials=creds, cache_discovery=False)

    def _parse_json_arg(self, text: str) -> dict[str, Any]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON must be an object")
        return value

    def _parse_dt(self, value: str) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty datetime")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return datetime.fromisoformat(text)
        return datetime.fromisoformat(text)

    def _event_time(self, value: str, timezone_name: str) -> dict[str, str]:
        text = str(value or "").strip()
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return {"date": text}
        return {"dateTime": text, "timeZone": timezone_name}

    def _default_end(self, start: str) -> str:
        dt = self._parse_dt(start)
        return (dt + timedelta(minutes=self._duration_minutes())).isoformat()

    def _task_due(self, due: str) -> str:
        text = str(due or "").strip()
        if not text:
            return ""
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return f"{text}T00:00:00.000Z"
        dt = self._parse_dt(text)
        if dt.tzinfo is None:
            # Google Tasks due is RFC3339 timestamp. Date part is what matters.
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    async def _create_calendar_event_impl(
        self,
        title: str,
        start: str,
        end: str = "",
        description: str = "",
        location: str = "",
        timezone_name: str = "",
        calendar_id: str = "",
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        start = str(start or "").strip()
        if not title:
            raise ValueError("title is required")
        if not start:
            raise ValueError("start is required")

        tz = self._timezone(timezone_name)
        final_end = str(end or "").strip() or self._default_end(start)
        cal_id = str(calendar_id or self._cfg("calendar_id", "primary") or "primary").strip()

        body: dict[str, Any] = {
            "summary": title,
            "start": self._event_time(start, tz),
            "end": self._event_time(final_end, tz),
        }
        if description:
            body["description"] = str(description)
        if location:
            body["location"] = str(location)

        service = await self._get_service("calendar", "v3")
        created = await asyncio.to_thread(
            lambda: service.events().insert(calendarId=cal_id, body=body).execute()
        )
        return {
            "ok": True,
            "id": created.get("id"),
            "summary": created.get("summary"),
            "htmlLink": created.get("htmlLink"),
            "start": created.get("start"),
            "end": created.get("end"),
        }

    async def _create_task_impl(
        self,
        title: str,
        notes: str = "",
        due: str = "",
        tasklist_id: str = "",
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("title is required")
        list_id = str(tasklist_id or self._cfg("tasklist_id", "@default") or "@default").strip()

        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = str(notes)
        due_text = self._task_due(due)
        if due_text:
            body["due"] = due_text

        service = await self._get_service("tasks", "v1")
        created = await asyncio.to_thread(
            lambda: service.tasks().insert(tasklist=list_id, body=body).execute()
        )
        return {
            "ok": True,
            "id": created.get("id"),
            "title": created.get("title"),
            "due": created.get("due"),
            "selfLink": created.get("selfLink"),
        }

    def _json_result(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    @filter.command("gagenda_status")
    async def gagenda_status(self, event: AstrMessageEvent):
        ok, dep_msg = self._deps_ok()
        token_path = self._token_path()
        lines = [
            f"dependencies: {'ok' if ok else dep_msg}",
            f"credentials_path: {self._credentials_path() or '(not configured)'}",
            f"token_path: {token_path}",
            f"token_exists: {os.path.exists(token_path)}",
            f"calendar_id: {self._cfg('calendar_id', 'primary')}",
            f"tasklist_id: {self._cfg('tasklist_id', '@default')}",
            f"timezone: {self._timezone()}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("gagenda_auth")
    async def gagenda_auth(self, event: AstrMessageEvent):
        async with self._auth_lock:
            try:
                result = await asyncio.to_thread(self._auth_sync)
            except Exception as exc:
                logger.exception("[GoogleAgenda] auth failed")
                result = f"Google authorization failed: {exc}"
        yield event.plain_result(result)

    @filter.command("gcal_event")
    async def gcal_event(self, event: AstrMessageEvent):
        if not self._cfg("enable_command_fallback", True):
            yield event.plain_result("Command fallback is disabled.")
            return
        raw = event.message_str.strip()
        if not raw:
            yield event.plain_result('Usage: /gcal_event {"title":"...","start":"2026-06-16T15:00:00"}')
            return
        try:
            data = self._parse_json_arg(raw)
            result = await self._create_calendar_event_impl(
                title=data.get("title", ""),
                start=data.get("start", ""),
                end=data.get("end", ""),
                description=data.get("description", ""),
                location=data.get("location", ""),
                timezone_name=data.get("timezone", ""),
                calendar_id=data.get("calendar_id", ""),
            )
            yield event.plain_result("Created calendar event:\n" + self._json_result(result))
        except Exception as exc:
            yield event.plain_result(f"Failed to create calendar event: {exc}")

    @filter.command("gtask_create")
    async def gtask_create(self, event: AstrMessageEvent):
        if not self._cfg("enable_command_fallback", True):
            yield event.plain_result("Command fallback is disabled.")
            return
        raw = event.message_str.strip()
        if not raw:
            yield event.plain_result('Usage: /gtask_create {"title":"...","due":"2026-06-19"}')
            return
        try:
            data = self._parse_json_arg(raw)
            result = await self._create_task_impl(
                title=data.get("title", ""),
                notes=data.get("notes", ""),
                due=data.get("due", ""),
                tasklist_id=data.get("tasklist_id", ""),
            )
            yield event.plain_result("Created Google task:\n" + self._json_result(result))
        except Exception as exc:
            yield event.plain_result(f"Failed to create Google task: {exc}")

    @filter.llm_tool(name="create_google_calendar_event")
    async def create_google_calendar_event(
        self,
        event: AstrMessageEvent,
        title: str,
        start: str,
        end: str = "",
        description: str = "",
        location: str = "",
        timezone: str = "",
        calendar_id: str = "",
    ) -> str:
        """Create a Google Calendar event from a schedule-like user request.

        Args:
            title(string): Required. Event title/summary.
            start(string): Required. ISO date or datetime, e.g. 2026-06-16T15:00:00 or 2026-06-16.
            end(string): Optional. ISO date or datetime. If omitted, default duration is used.
            description(string): Optional. Event description/notes.
            location(string): Optional. Event location.
            timezone(string): Optional. IANA timezone, e.g. Asia/Shanghai. Defaults to plugin config.
            calendar_id(string): Optional. Google Calendar ID. Defaults to plugin config.
        """
        try:
            result = await self._create_calendar_event_impl(
                title=title,
                start=start,
                end=end,
                description=description,
                location=location,
                timezone_name=timezone,
                calendar_id=calendar_id,
            )
            return self._json_result(result)
        except Exception as exc:
            logger.exception("[GoogleAgenda] create event failed")
            return self._json_result({"ok": False, "error": str(exc)})

    @filter.llm_tool(name="create_google_task")
    async def create_google_task(
        self,
        event: AstrMessageEvent,
        title: str,
        notes: str = "",
        due: str = "",
        tasklist_id: str = "",
    ) -> str:
        """Create a Google Tasks todo item from a todo-like user request.

        Args:
            title(string): Required. Task title.
            notes(string): Optional. Task notes/details.
            due(string): Optional. ISO date or datetime, e.g. 2026-06-19.
            tasklist_id(string): Optional. Google Tasks tasklist ID. Defaults to plugin config.
        """
        try:
            result = await self._create_task_impl(
                title=title,
                notes=notes,
                due=due,
                tasklist_id=tasklist_id,
            )
            return self._json_result(result)
        except Exception as exc:
            logger.exception("[GoogleAgenda] create task failed")
            return self._json_result({"ok": False, "error": str(exc)})
