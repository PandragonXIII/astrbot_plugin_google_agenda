import asyncio
import getpass
import json
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from astrbot.api import AstrBotConfig, logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow, InstalledAppFlow
    from googleapiclient.discovery import build
except Exception:  # Dependencies may be absent before requirements.txt is installed.
    Request = None
    Credentials = None
    Flow = None
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
    "0.1.1",
)
class GoogleAgendaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._auth_lock = asyncio.Lock()
        self._auth_state: dict[str, Any] = {}

    # ---------- config / dependencies ----------

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _str_cfg(self, key: str, default: str = "") -> str:
        return str(self._cfg(key, default) or default).strip()

    def _int_cfg(self, key: str, default: int, minimum: int = 1) -> int:
        try:
            return max(minimum, int(self._cfg(key, default) or default))
        except Exception:
            return default

    def _deps_error(self) -> str:
        if all([Request, Credentials, Flow, InstalledAppFlow, build]):
            return ""
        return "Google API dependencies are missing. Install requirements.txt in AstrBot's Python environment."

    @property
    def token_path(self) -> str:
        return str(Path(get_astrbot_data_path()) / "plugin_data" / self.name / "token.json")

    @property
    def credentials_path(self) -> str:
        return self._str_cfg("credentials_path")

    # ---------- Google auth ----------

    def _validate_google_config(self) -> None:
        dep_error = self._deps_error()
        if dep_error:
            raise RuntimeError(dep_error)
        if not self.credentials_path:
            raise RuntimeError("credentials_path is not configured.")
        if not os.path.exists(self.credentials_path):
            raise RuntimeError(f"credentials_path does not exist: {self.credentials_path}")

    def _save_token(self, creds) -> None:
        os.makedirs(os.path.dirname(self.token_path) or ".", exist_ok=True)
        with open(self.token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass

    def _load_credentials(self):
        self._validate_google_config()
        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_token(creds)
        if not creds or not creds.valid:
            raise RuntimeError("Google token is missing or invalid. Run /gagenda_auth first.")
        return creds

    def _ssh_target(self) -> str:
        configured = self._str_cfg("auth_ssh_target")
        if configured:
            return configured
        return f"{getpass.getuser()}@{socket.gethostname()}"

    def _load_oauth_flow(self, redirect_uri: str) -> "InstalledAppFlow | Flow":
        """Detect installed vs web OAuth client and return the correct flow."""
        with open(self.credentials_path, "r", encoding="utf-8") as f:
            creds_data = json.load(f)

        if "installed" in creds_data:
            flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
        elif "web" in creds_data:
            flow = Flow.from_client_secrets_file(self.credentials_path, SCOPES)
        else:
            raise RuntimeError(
                "Unable to detect OAuth client type from credentials file. "
                "Expected top-level key 'installed' or 'web'."
            )
        flow.redirect_uri = redirect_uri
        return flow

    def _start_oauth_listener(self) -> str:
        self._validate_google_config()

        port = self._int_cfg("auth_port", 8765, minimum=1)
        timeout = self._int_cfg("auth_timeout_seconds", 300, minimum=30)
        redirect_uri = f"http://127.0.0.1:{port}/"

        flow = self._load_oauth_flow(redirect_uri)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )

        state = {"done": False, "ok": False, "message": "Authorization pending."}
        self._auth_state = state

        plugin = self

        class OAuthCallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                body = "Google authorization failed. You can close this page."
                try:
                    params = parse_qs(urlparse(self.path).query)
                    if "error" in params:
                        raise RuntimeError(params["error"][0])
                    code = (params.get("code") or [""])[0]
                    if not code:
                        raise RuntimeError("OAuth callback missing code.")

                    flow.fetch_token(code=code)
                    plugin._save_token(flow.credentials)
                    state.update(
                        done=True,
                        ok=True,
                        message=f"Google authorization succeeded. Token saved to {plugin.token_path}",
                    )
                    body = "Google authorization succeeded. You can close this page and return to QQ."
                except Exception as exc:
                    state.update(done=True, ok=False, message=str(exc))
                    body = f"Google authorization failed: {exc}"

                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        def serve_once() -> None:
            try:
                httpd = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
                httpd.timeout = timeout
                httpd.handle_request()
                if not state["done"]:
                    state.update(done=True, ok=False, message="Authorization timed out. Run /gagenda_auth again.")
            except Exception as exc:
                state.update(done=True, ok=False, message=str(exc))

        threading.Thread(target=serve_once, daemon=True).start()
        ssh_cmd = f"ssh -L {port}:127.0.0.1:{port} {self._ssh_target()}"
        return (
            "Google OAuth over SSH tunnel\n\n"
            "1) On YOUR LOCAL COMPUTER, run this command first and keep it open:\n"
            f"{ssh_cmd}\n\n"
            "2) Then open this Google authorization URL in your LOCAL browser:\n"
            f"{auth_url}\n\n"
            "3) After approval, Google redirects to local 127.0.0.1; SSH forwards it to AstrBot, "
            "and the plugin saves token automatically.\n"
            f"Timeout: {timeout}s. Check with /gagenda_auth_status.\n\n"
            "If the SSH target is wrong, set plugin config auth_ssh_target, e.g. user@server-ip."
        )

    async def _google_service(self, name: str, version: str):
        creds = await asyncio.to_thread(self._load_credentials)
        return await asyncio.to_thread(build, name, version, credentials=creds, cache_discovery=False)

    # ---------- parsing / formatting ----------

    def _json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _command_payload(self, message: str, command: str) -> str:
        raw = str(message or "").strip()
        for prefix in (f"/{command}", command):
            if raw == prefix:
                return ""
            if raw.startswith(prefix + " "):
                return raw[len(prefix) :].strip()
        return raw

    def _json_payload(self, message: str, command: str) -> dict[str, Any]:
        raw = self._command_payload(message, command)
        if not raw:
            raise ValueError("missing JSON payload")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("JSON payload must be an object")
        return value

    def _parse_datetime(self, value: str) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty datetime")
        return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)

    def _event_time(self, value: str, tz: str) -> dict[str, str]:
        text = str(value or "").strip()
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return {"date": text}
        return {"dateTime": text, "timeZone": tz}

    def _task_due(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return f"{text}T00:00:00.000Z"
        dt = self._parse_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # ---------- Google API operations ----------

    async def _create_event(
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

        tz = timezone_name or self._str_cfg("default_timezone", "Asia/Shanghai")
        duration = self._int_cfg("default_event_duration_minutes", 60, minimum=1)
        final_end = str(end or "").strip() or (self._parse_datetime(start) + timedelta(minutes=duration)).isoformat()
        cal_id = calendar_id or self._str_cfg("calendar_id", "primary")

        body: dict[str, Any] = {
            "summary": title,
            "start": self._event_time(start, tz),
            "end": self._event_time(final_end, tz),
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        service = await self._google_service("calendar", "v3")
        created = await asyncio.to_thread(lambda: service.events().insert(calendarId=cal_id, body=body).execute())
        return {
            "ok": True,
            "id": created.get("id"),
            "summary": created.get("summary"),
            "htmlLink": created.get("htmlLink"),
            "start": created.get("start"),
            "end": created.get("end"),
        }

    async def _create_task(self, title: str, notes: str = "", due: str = "", tasklist_id: str = "") -> dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ValueError("title is required")

        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        due_text = self._task_due(due)
        if due_text:
            body["due"] = due_text

        service = await self._google_service("tasks", "v1")
        list_id = tasklist_id or self._str_cfg("tasklist_id", "@default")
        created = await asyncio.to_thread(lambda: service.tasks().insert(tasklist=list_id, body=body).execute())
        return {
            "ok": True,
            "id": created.get("id"),
            "title": created.get("title"),
            "due": created.get("due"),
            "selfLink": created.get("selfLink"),
        }

    # ---------- commands ----------

    @filter.command("gagenda_status")
    async def gagenda_status(self, event: AstrMessageEvent):
        lines = [
            f"dependencies: {'ok' if not self._deps_error() else self._deps_error()}",
            f"credentials_path: {self.credentials_path or '(not configured)'}",
            f"token_path: {self.token_path}",
            f"token_exists: {os.path.exists(self.token_path)}",
            f"calendar_id: {self._str_cfg('calendar_id', 'primary')}",
            f"tasklist_id: {self._str_cfg('tasklist_id', '@default')}",
            f"timezone: {self._str_cfg('default_timezone', 'Asia/Shanghai')}",
            f"auth_port: {self._int_cfg('auth_port', 8765)}",
            f"auth_ssh_target: {self._ssh_target()}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("gagenda_auth")
    async def gagenda_auth(self, event: AstrMessageEvent):
        async with self._auth_lock:
            try:
                result = await asyncio.to_thread(self._start_oauth_listener)
            except Exception as exc:
                logger.exception("[GoogleAgenda] auth URL generation failed")
                result = f"Google authorization URL generation failed: {exc}"
        yield event.plain_result(result)

    @filter.command("gagenda_auth_status")
    async def gagenda_auth_status(self, event: AstrMessageEvent):
        if not self._auth_state:
            yield event.plain_result("No pending /gagenda_auth authorization flow.")
            return
        if not self._auth_state.get("done"):
            yield event.plain_result("Authorization is still pending. Keep SSH tunnel open and finish Google login.")
            return
        status = "ok" if self._auth_state.get("ok") else "failed"
        yield event.plain_result(f"Authorization {status}: {self._auth_state.get('message', '')}")

    @filter.command("gcal_event")
    async def gcal_event(self, event: AstrMessageEvent):
        if not self._cfg("enable_command_fallback", True):
            yield event.plain_result("Command fallback is disabled.")
            return
        try:
            data = self._json_payload(event.message_str, "gcal_event")
            result = await self._create_event(
                title=data.get("title", ""),
                start=data.get("start", ""),
                end=data.get("end", ""),
                description=data.get("description", ""),
                location=data.get("location", ""),
                timezone_name=data.get("timezone", ""),
                calendar_id=data.get("calendar_id", ""),
            )
            yield event.plain_result("Created calendar event:\n" + self._json(result))
        except Exception as exc:
            yield event.plain_result(f"Failed to create calendar event: {exc}")

    @filter.command("gtask_create")
    async def gtask_create(self, event: AstrMessageEvent):
        if not self._cfg("enable_command_fallback", True):
            yield event.plain_result("Command fallback is disabled.")
            return
        try:
            data = self._json_payload(event.message_str, "gtask_create")
            result = await self._create_task(
                title=data.get("title", ""),
                notes=data.get("notes", ""),
                due=data.get("due", ""),
                tasklist_id=data.get("tasklist_id", ""),
            )
            yield event.plain_result("Created Google task:\n" + self._json(result))
        except Exception as exc:
            yield event.plain_result(f"Failed to create Google task: {exc}")

    # ---------- LLM tools ----------

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
            return self._json(await self._create_event(title, start, end, description, location, timezone, calendar_id))
        except Exception as exc:
            logger.exception("[GoogleAgenda] create event failed")
            return self._json({"ok": False, "error": str(exc)})

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
            return self._json(await self._create_task(title, notes, due, tasklist_id))
        except Exception as exc:
            logger.exception("[GoogleAgenda] create task failed")
            return self._json({"ok": False, "error": str(exc)})
