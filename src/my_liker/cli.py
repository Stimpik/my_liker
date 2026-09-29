"""Command-line interface for safe Stepik automation setup."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .browser import AuthenticationRequired, StepikBrowser, StepikRequestError
from .notifications import NotificationKind, classify_notification
from .settings import Settings, load_settings
from .stats import StatsStore


DEFAULT_PROFILE_DIR = Path(".stepik-profile")
DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_DATABASE_PATH = Path("data/stats.sqlite3")
DEFAULT_PLAN_CACHE_PATH = Path("data/plan.json")
PLAN_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class PlannedAction:
    kind: str
    notification: dict[str, Any]
    actor_id: int
    actor_name: str
    reason: str
    comment_id: int | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my-liker")
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help="directory containing the isolated persistent Chrome profile",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--plan-cache", type=Path, default=DEFAULT_PLAN_CACHE_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="open Chrome for a manual Stepik login")

    scan = subparsers.add_parser(
        "scan",
        help="read and classify unread notifications without changing anything",
    )
    scan.add_argument(
        "--visible",
        action="store_true",
        help="show Chrome while scanning",
    )
    apply_parser = subparsers.add_parser(
        "apply", help="preview or execute safe actions from unread notifications"
    )
    apply_parser.add_argument(
        "--yes",
        action="store_true",
        help="execute the displayed actions; without this flag only preview them",
    )
    apply_parser.add_argument("--visible", action="store_true")

    stats = subparsers.add_parser("stats", help="show locally collected statistics")
    stats.add_argument("--export", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    store = StatsStore(args.database)
    try:
        if args.command == "login":
            return _login(args.profile_dir)
        if args.command == "scan":
            return _scan(
                args.profile_dir,
                settings=settings,
                store=store,
                plan_cache_path=args.plan_cache,
                visible=args.visible,
            )
        if args.command == "apply":
            return _apply(
                args.profile_dir,
                settings=settings,
                store=store,
                plan_cache_path=args.plan_cache,
                execute=args.yes,
                visible=args.visible,
            )
        if args.command == "stats":
            return _stats(store, export_path=args.export)
    except (AuthenticationRequired, StepikRequestError) as error:
        print(f"Error: {error}")
        return 2
    finally:
        store.close()
    return 1


def _login(profile_dir: Path) -> int:
    with StepikBrowser(profile_dir, headless=False) as browser:
        browser.open_notifications()
        input(
            "Log in to Stepik in the opened Chrome window, open Notifications, "
            "then press Enter here..."
        )
        notifications = browser.unread_notifications()
        browser.save_storage_state()
    print(f"Session saved. Unread notifications available: {len(notifications)}")
    return 0


def _scan(
    profile_dir: Path,
    *,
    settings: Settings,
    store: StatsStore,
    plan_cache_path: Path,
    visible: bool,
) -> int:
    with StepikBrowser(profile_dir, headless=not visible) as browser:
        # Loading the site first lets Stepik restore/refresh the authenticated
        # web session before the browser-bound API context is used.
        browser.open_notifications()
        notifications = browser.unread_notifications(progress=_print_page_progress)

        counts: Counter[NotificationKind] = Counter()
        for notification in notifications:
            kind = classify_notification(notification)
            counts[kind] += 1
            if kind in {
                NotificationKind.SOLUTION_VOTE,
                NotificationKind.COMMENT_VOTE,
            }:
                store.record_received_like(notification)
            print(_format_notification(notification, kind))

        actions = _build_action_plan(
            browser, notifications, settings, show_progress=True
        )
        _save_plan_cache(plan_cache_path, actions)

    print("\nSummary (dry-run; no account data changed):")
    for kind in NotificationKind:
        print(f"  {kind.value}: {counts[kind]}")
    print(f"Cached {len(actions)} planned actions in {plan_cache_path}")
    return 0


def _apply(
    profile_dir: Path,
    *,
    settings: Settings,
    store: StatsStore,
    plan_cache_path: Path,
    execute: bool,
    visible: bool,
) -> int:
    cached_actions = _load_plan_cache(plan_cache_path)
    if execute and cached_actions is None:
        print("No fresh cached plan. Run 'scan' or 'apply' first, then retry.")
        return 2

    if cached_actions is not None:
        print(f"Using fresh cached plan from {plan_cache_path}")
        actions = cached_actions
        if not execute:
            return _show_preview(actions, settings.max_actions_per_run)
    else:
        actions = []

    with StepikBrowser(profile_dir, headless=not visible) as browser:
        if not execute:
            browser.open_notifications()
            notifications = browser.unread_notifications(progress=_print_page_progress)
            for notification in notifications:
                kind = classify_notification(notification)
                if kind in {
                    NotificationKind.SOLUTION_VOTE,
                    NotificationKind.COMMENT_VOTE,
                }:
                    store.record_received_like(notification)
            actions = _build_action_plan(
                browser, notifications, settings, show_progress=True
            )
            _save_plan_cache(plan_cache_path, actions)
            return _show_preview(actions, settings.max_actions_per_run)

        browser.open_notifications()
        batch = actions[: settings.max_actions_per_run]
        for action in batch:
            if action.kind == "mark_read":
                browser.mark_notification_read(action.notification)
            elif action.kind == "like_and_mark_read":
                assert action.comment_id is not None
                vote_id, vote_value = browser.solution_vote_state(action.comment_id)
                if vote_id is None:
                    browser.like_solution(action.comment_id)
                    store.record_given_like(
                        actor_id=action.actor_id,
                        actor_name=action.actor_name,
                        comment_id=action.comment_id,
                    )
                elif vote_value != "epic":
                    print(
                        f"skipped {_format_action(action)} "
                        f"because current vote is {vote_value!r}"
                    )
                    continue
                browser.mark_notification_read(action.notification)
            elif action.kind == "like_preferred":
                assert action.comment_id is not None
                vote_id, vote_value = browser.solution_vote_state(action.comment_id)
                if vote_id is None:
                    browser.like_solution(action.comment_id)
                    store.record_given_like(
                        actor_id=action.actor_id,
                        actor_name=action.actor_name,
                        comment_id=action.comment_id,
                    )
                elif vote_value != "epic":
                    print(
                        f"skipped {_format_action(action)} "
                        f"because current vote is {vote_value!r}"
                    )
                    continue
            print(f"executed {_format_action(action)}")
    remaining = actions[len(batch) :]
    if remaining:
        _save_plan_cache(plan_cache_path, remaining)
        print(
            f"Batch complete. {len(remaining)} cached actions remain; "
            "run 'apply' to preview the next batch."
        )
    else:
        plan_cache_path.unlink(missing_ok=True)
        print("Plan complete; cache removed.")
    return 0


def _build_action_plan(
    browser: StepikBrowser,
    notifications: list[dict[str, Any]],
    settings: Settings,
    *,
    show_progress: bool = False,
) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    solution_step_ids: set[int] = set()
    planned_comment_ids: set[int] = set()
    solution_checks = sum(
        classify_notification(item) is NotificationKind.SOLUTION_VOTE
        for item in notifications
    )
    checked_solutions = 0
    for notification in notifications:
        kind = classify_notification(notification)
        context = notification.get("context", {})
        data = context.get("data", {})
        actor_id = data.get("actor_id")
        actor_name = str(data.get("actor_name") or actor_id or "-")

        if kind is NotificationKind.COMMENT_VOTE and isinstance(actor_id, int):
            actions.append(
                PlannedAction(
                    "mark_read",
                    notification,
                    actor_id,
                    actor_name,
                    "comment_vote",
                )
            )
            continue

        if kind is not NotificationKind.SOLUTION_VOTE or not isinstance(actor_id, int):
            continue
        checked_solutions += 1
        if show_progress:
            print(
                f"Checking solution {checked_solutions}/{solution_checks}: "
                f"actor={actor_name!r}",
                flush=True,
            )
        if settings.is_blacklisted(actor_id):
            actions.append(
                PlannedAction(
                    "mark_read",
                    notification,
                    actor_id,
                    actor_name,
                    "blacklisted_author",
                )
            )
            continue

        target = context.get("target", {})
        step_id = target.get("step_id")
        if not isinstance(step_id, int):
            continue
        solution_step_ids.add(step_id)
        solution = browser.find_solution(step_id=step_id, user_id=actor_id)
        if solution is None:
            actions.append(
                PlannedAction(
                    "mark_read",
                    notification,
                    actor_id,
                    actor_name,
                    "author_has_no_solution",
                )
            )
            continue
        if solution.get("_current_vote_value") == "epic":
            actions.append(
                PlannedAction(
                    "mark_read",
                    notification,
                    actor_id,
                    actor_name,
                    "already_liked",
                    solution.get("id"),
                )
            )
            continue
        if solution.get("vote") is not None:
            # An existing non-positive or unknown vote needs manual review.
            continue
        comment_id = solution.get("id")
        if isinstance(comment_id, int):
            actions.append(
                PlannedAction(
                    "like_and_mark_read",
                    notification,
                    actor_id,
                    actor_name,
                    "reciprocal_like",
                    comment_id,
                )
            )
            planned_comment_ids.add(comment_id)

    if settings.preferred_authors_enabled:
        for step_id in sorted(solution_step_ids):
            for actor_id in sorted(settings.preferred_authors):
                if settings.is_blacklisted(actor_id):
                    continue
                solution = browser.find_solution(step_id=step_id, user_id=actor_id)
                if solution is None or solution.get("vote") is not None:
                    continue
                comment_id = solution.get("id")
                if not isinstance(comment_id, int) or comment_id in planned_comment_ids:
                    continue
                actions.append(
                    PlannedAction(
                        "like_preferred",
                        {},
                        actor_id,
                        str(solution.get("_user_name") or actor_id),
                        "preferred_author",
                        comment_id,
                    )
                )
                planned_comment_ids.add(comment_id)
    return actions


def _show_preview(actions: list[PlannedAction], batch_size: int) -> int:
    if not actions:
        print("No actions to execute.")
        return 0
    batch = actions[:batch_size]
    print(f"Next batch: {len(batch)} of {len(actions)} cached actions")
    for action in batch:
        print(_format_action(action))
    print("Preview only. Re-run with 'apply --yes' within 15 minutes to execute.")
    return 0


def _save_plan_cache(path: Path, actions: list[PlannedAction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "actions": [
            {
                "kind": action.kind,
                "notification": action.notification,
                "actor_id": action.actor_id,
                "actor_name": action.actor_name,
                "reason": action.reason,
                "comment_id": action.comment_id,
            }
            for action in actions
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_plan_cache(path: Path) -> list[PlannedAction] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(payload["created_at"])
        if datetime.now(UTC) - created_at > PLAN_TTL:
            return None
        return [PlannedAction(**item) for item in payload.get("actions", [])]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _print_page_progress(page: int, total: int) -> None:
    print(
        f"Loaded notification page {page}; unread collected: {total}",
        flush=True,
    )


def _format_action(action: PlannedAction) -> str:
    notification_id = action.notification.get("id")
    details = (
        f"action={action.kind} notification={notification_id} "
        f"actor={action.actor_name!r} actor_id={action.actor_id} "
        f"reason={action.reason}"
    )
    if action.comment_id is not None:
        details += f" comment_id={action.comment_id}"
    return details


def _stats(store: StatsStore, *, export_path: Path | None) -> int:
    aggregated = store.aggregate()
    if not aggregated:
        print("No statistics collected yet.")
    for actor_id, values in aggregated.items():
        print(
            f"actor={values['name']!r} actor_id={actor_id} "
            f"received={values['received_likes']} given={values['given_likes']}"
        )
    if export_path is not None:
        store.export_json(export_path)
        print(f"Exported statistics to {export_path}")
    return 0


def _format_notification(
    notification: dict[str, Any], kind: NotificationKind
) -> str:
    data = notification.get("context", {}).get("data", {})
    actor = data.get("actor_name") or "-"
    actor_id = data.get("actor_id") or "-"
    notification_id = notification.get("id") or "-"
    return (
        f"notification={notification_id} kind={kind.value} "
        f"actor={actor!r} actor_id={actor_id}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
