from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from ticktick_mcp.client import TickTickClient
from ticktick_mcp.models import Filter
from ticktick_mcp.resolve import resolve_name_with_etag
from ticktick_mcp.tools.tasks import _convert


def _get_client(ctx: Context) -> TickTickClient:
    return ctx.request_context.lifespan_context["client"]  # type: ignore[union-attr]


async def _resolve_filter(client: TickTickClient, name_or_id: str) -> tuple[str, str]:
    """Resolve a filter name/ID to (id, etag)."""
    data = await client.batch_check()
    filters = [Filter(**f) for f in data.get("filters") or []]
    return resolve_name_with_etag(
        name_or_id,
        filters,
        lambda f: f.name,
        lambda f: f.id,
        lambda f: f.etag or "",
        "filter",
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_filters(ctx: Context) -> list[dict[str, Any]]:
        """List all saved filters.

        Returns all custom filters with their IDs, names, rules, and sort settings.
        Requires v2 session token.
        """
        client = _get_client(ctx)
        data = await client.batch_check()
        return data.get("filters") or []

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def get_filter_tasks(
        ctx: Context,
        filter_name: str,
    ) -> list[dict[str, Any]]:
        """Get tasks matching a saved filter.

        Reads the filter's current rule from TickTick and returns all active tasks
        that match its conditions. Updating the filter in the app will automatically
        be reflected in subsequent calls.

        Handles the following condition types:
        - listOrGroup / list: project membership
        - dueDate: nodue, today, overdue, span(~N) (due within N days)
        - assignee: noassignee, me
        - taskType: task (excludes checklist items)

        Args:
            filter_name: Filter name or ID. Supports fuzzy matching.
        """
        client = _get_client(ctx)
        data = await client.batch_check()

        # Resolve filter
        filters_data = data.get("filters") or []
        filter_obj: dict[str, Any] | None = None
        lower = filter_name.lower()
        for f in filters_data:
            if f.get("id") == filter_name or f.get("name", "").lower() == lower:
                filter_obj = f
                break
        if filter_obj is None:
            for f in filters_data:
                if lower in f.get("name", "").lower():
                    filter_obj = f
                    break
        if filter_obj is None:
            raise ToolError(f"Filter '{filter_name}' not found")

        rule_str = filter_obj.get("rule")
        if not rule_str:
            raise ToolError(f"Filter '{filter_name}' has no rule defined")

        rule = json.loads(rule_str)

        # Parse conditions
        project_ids: list[str] | None = None
        date_conditions: list[str] = []
        assignee_conditions: list[str] = []
        task_type_conditions: list[str] = []

        for condition in rule.get("and", []):
            cname = condition.get("conditionName")
            if cname == "listOrGroup":
                project_ids = []
                for item in condition.get("or", []):
                    if isinstance(item, dict) and item.get("conditionName") == "list":
                        project_ids.extend(item.get("or", []))
            elif cname == "dueDate":
                date_conditions = condition.get("or", [])
            elif cname == "assignee":
                assignee_conditions = condition.get("or", [])
            elif cname == "taskType":
                task_type_conditions = condition.get("or", [])

        # Get all tasks from batch_check (v2, includes assigneeId)
        sync_bean = data.get("syncTaskBean") or {}
        all_tasks: list[dict[str, Any]] = sync_bean.get("update") or []

        # Filter to relevant projects if specified
        if project_ids:
            project_id_set = set(project_ids)
            all_tasks = [t for t in all_tasks if t.get("projectId") in project_id_set]

        # Get current user ID for "me" assignee condition
        user_id: int | None = None
        if "me" in assignee_conditions:
            # Try profile fields first
            for key in ("profile", "userProfile", "user"):
                p = data.get(key) or {}
                raw = p.get("userId") or p.get("id")
                if raw:
                    try:
                        user_id = int(raw)
                    except (ValueError, TypeError):
                        user_id = raw
                    break
            # Fallback: extract from inbox project ID (format "inbox{userId}")
            if not user_id:
                for pid in (project_ids or []) + [
                    proj.get("id", "") for proj in (data.get("projectProfiles") or [])
                ]:
                    if isinstance(pid, str) and pid.startswith("inbox") and pid[5:].isdigit():
                        user_id = int(pid[5:])
                        break

        now = datetime.now(timezone.utc)

        def _parse_due(due: str) -> datetime | None:
            try:
                return datetime.fromisoformat(due.replace("Z", "+00:00"))
            except ValueError:
                return None

        def check_date(task: dict[str, Any]) -> bool:
            if not date_conditions:
                return True
            due = task.get("dueDate")
            for cond in date_conditions:
                if cond == "nodue" and not due:
                    return True
                elif due:
                    dt = _parse_due(due)
                    if dt is None:
                        continue
                    if cond == "today" and dt.date() == now.date():
                        return True
                    elif cond == "overdue" and dt < now:
                        return True
                    elif cond.startswith("span(~"):
                        try:
                            days = int(cond[6:-1])
                            if dt <= now + timedelta(days=days):
                                return True
                        except ValueError:
                            pass
            return False

        def check_assignee(task: dict[str, Any]) -> bool:
            if not assignee_conditions:
                return True
            assignee = task.get("assignee")
            for cond in assignee_conditions:
                if cond == "noassignee" and not assignee:
                    return True
                elif cond == "me" and assignee and assignee == user_id:
                    return True
            return False

        def check_task_type(task: dict[str, Any]) -> bool:
            if not task_type_conditions or "task" in task_type_conditions:
                return True
            return False

        return [
            _convert(t) for t in all_tasks
            if t.get("status") == 0
            and check_date(t)
            and check_assignee(t)
            and check_task_type(t)
        ]
