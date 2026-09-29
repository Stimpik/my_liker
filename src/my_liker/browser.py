"""Persistent browser session used to access Stepik as the user."""

from __future__ import annotations

from contextlib import AbstractContextManager
import json
from pathlib import Path
import time
from typing import Any, Callable
from urllib.parse import urlencode

from playwright.sync_api import BrowserContext, Playwright, sync_playwright


STEPIK_ORIGIN = "https://stepik.org"


class StepikBrowser(AbstractContextManager["StepikBrowser"]):
    """Open an isolated, persistent Chrome profile for Stepik."""

    def __init__(self, profile_dir: Path, *, headless: bool) -> None:
        self.profile_dir = profile_dir
        self.storage_state_path = profile_dir / "storage-state.json"
        self.headless = headless
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self._step_solutions_cache: dict[int, dict[int, dict[str, Any]]] = {}

    def __enter__(self) -> "StepikBrowser":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            channel="chrome",
            headless=self.headless,
        )
        self._restore_storage_state()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self.context is not None:
            self.context.close()
        if self._playwright is not None:
            self._playwright.stop()

    def open_notifications(self) -> None:
        context = self._require_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"{STEPIK_ORIGIN}/notifications", wait_until="domcontentloaded")

    def unread_notifications(
        self, progress: Callable[[int, int], None] | None = None
    ) -> list[dict[str, Any]]:
        notifications: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._get_json(
                f"{STEPIK_ORIGIN}/api/notifications",
                "notifications",
                params={
                    "is_unread": "true",
                    "order": "-time,-id",
                    "page": str(page),
                },
            )
            page_items = payload.get("notifications")
            if not isinstance(page_items, list):
                raise StepikRequestError(
                    "Stepik returned an unexpected notifications payload."
                )
            notifications.extend(
                item for item in page_items if isinstance(item, dict)
            )
            if progress is not None:
                progress(page, len(notifications))
            meta = payload.get("meta", {})
            if not meta.get("has_next"):
                return notifications
            page += 1
            time.sleep(0.35)

    def save_storage_state(self) -> None:
        """Persist cookies and web storage after the interactive login."""

        context = self._require_context()
        context.storage_state(path=self.storage_state_path)

    def find_solution(self, *, step_id: int, user_id: int) -> dict[str, Any] | None:
        """Find a user's solution comment in the solution forum for a step."""

        cached = self._step_solutions_cache.get(step_id)
        if cached is not None:
            return cached.get(user_id)

        proxy_id = f"77-{step_id}-2"
        payload = self._get_json(
            f"{STEPIK_ORIGIN}/api/discussion-proxies/{proxy_id}",
            "discussion proxy",
        )
        proxies = payload.get("discussion-proxies", [])
        if not proxies:
            self._step_solutions_cache[step_id] = {}
            return None

        discussion_ids = proxies[0].get("discussions_recent_activity", [])
        if not discussion_ids:
            discussion_ids = proxies[0].get("discussions", [])

        solutions: dict[int, dict[str, Any]] = {}
        for offset in range(0, len(discussion_ids), 50):
            batch = discussion_ids[offset : offset + 50]
            query = urlencode([("ids[]", str(comment_id)) for comment_id in batch])
            comments_payload = self._get_json(
                f"{STEPIK_ORIGIN}/api/comments?{query}", "comments"
            )
            votes = {
                vote.get("id"): vote.get("value")
                for vote in comments_payload.get("votes", [])
                if isinstance(vote, dict)
            }
            users = {
                user.get("id"): user
                for user in comments_payload.get("users", [])
                if isinstance(user, dict)
            }
            for comment in comments_payload.get("comments", []):
                comment_user_id = comment.get("user")
                if (
                    not isinstance(comment_user_id, int)
                    or comment.get("thread") != "solutions"
                    or comment.get("is_deleted", False)
                    or comment.get("is_banned", False)
                ):
                    continue
                result = dict(comment)
                vote_id = comment.get("vote")
                result["_current_vote_value"] = self._vote_value(vote_id, votes)
                user = users.get(comment_user_id, {})
                result["_user_name"] = (
                    user.get("full_name")
                    or user.get("name")
                    or str(comment_user_id)
                )
                solutions.setdefault(comment_user_id, result)

        self._step_solutions_cache[step_id] = solutions
        return solutions.get(user_id)

    def mark_notification_read(self, notification: dict[str, Any]) -> None:
        notification_id = notification.get("id")
        if not isinstance(notification_id, int):
            raise StepikRequestError("Notification has no integer id.")
        updated = dict(notification)
        updated["is_unread"] = False
        response = self._require_context().request.put(
            f"{STEPIK_ORIGIN}/api/notifications/{notification_id}",
            data={"notification": updated},
            headers=self._write_headers(),
        )
        self._json_response(response, "mark notification read")

    def like_solution(self, comment_id: int) -> None:
        response = self._require_context().request.put(
            f"{STEPIK_ORIGIN}/api/votes/31-{comment_id}",
            data={"vote": {"value": "epic"}},
            headers=self._write_headers(),
        )
        self._json_response(response, "like solution")

    def solution_vote_state(self, comment_id: int) -> tuple[str | None, str | None]:
        """Return the current user's vote id and value for one solution."""

        query = urlencode([("ids[]", str(comment_id))])
        payload = self._get_json(
            f"{STEPIK_ORIGIN}/api/comments?{query}", "comment vote state"
        )
        comments = payload.get("comments", [])
        if not comments:
            raise StepikRequestError(f"Solution comment {comment_id} no longer exists.")
        vote_id = comments[0].get("vote")
        if vote_id is None:
            return None, None
        votes = {
            vote.get("id"): vote.get("value")
            for vote in payload.get("votes", [])
            if isinstance(vote, dict)
        }
        return str(vote_id), self._vote_value(vote_id, votes)

    @staticmethod
    def _vote_value(vote_id: Any, votes: dict[Any, Any]) -> str | None:
        value = votes.get(vote_id)
        if isinstance(value, str):
            return value
        # Stepik sometimes omits the side-loaded vote object. Vote type 31 is
        # the positive "epic" reaction used by /api/votes/31-{comment_id}.
        if isinstance(vote_id, str) and vote_id.startswith("31-"):
            return "epic"
        return None

    def _restore_storage_state(self) -> None:
        """Restore cookies that Chrome treats as session-only."""

        context = self._require_context()
        if not self.storage_state_path.exists():
            return
        state = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        cookies = state.get("cookies", [])
        if cookies:
            context.add_cookies(cookies)

    def _write_headers(self) -> dict[str, str]:
        cookies = self._require_context().cookies(STEPIK_ORIGIN)
        csrf_token = next(
            (cookie["value"] for cookie in cookies if cookie["name"] == "csrftoken"),
            None,
        )
        headers = {"Referer": f"{STEPIK_ORIGIN}/"}
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
        return headers

    def _get_json(
        self,
        url: str,
        description: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        delays = (2, 5, 15)
        for attempt in range(len(delays) + 1):
            response = self._require_context().request.get(url, params=params)
            if response.status != 429 and response.status < 500:
                return self._json_response(response, description)
            if attempt == len(delays):
                return self._json_response(response, description)

            retry_after = response.headers.get("retry-after")
            try:
                delay = min(float(retry_after), 60.0) if retry_after else delays[attempt]
            except ValueError:
                delay = delays[attempt]
            time.sleep(delay)

        raise AssertionError("retry loop must return")

    @staticmethod
    def _json_response(response: Any, description: str) -> dict[str, Any]:
        if response.status in {401, 403}:
            raise AuthenticationRequired(
                "Stepik session is missing or expired; run the login command."
            )
        if not response.ok:
            raise StepikRequestError(
                f"Stepik {description} request returned HTTP {response.status}: "
                f"{response.status_text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise StepikRequestError(
                f"Stepik returned an unexpected {description} payload."
            )
        return payload

    def _require_context(self) -> BrowserContext:
        if self.context is None:
            raise RuntimeError("Browser context is not open.")
        return self.context


class AuthenticationRequired(RuntimeError):
    """Raised when the persistent profile has no valid Stepik session."""


class StepikRequestError(RuntimeError):
    """Raised for an unexpected Stepik API response."""
