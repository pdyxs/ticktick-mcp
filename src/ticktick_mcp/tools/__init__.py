from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    from ticktick_mcp.tools import (
        calendar,
        filters,
        projects,
        tags,
        tasks,
    )

    tasks.register(mcp)
    projects.register(mcp)
    tags.register(mcp)
    filters.register(mcp)
    calendar.register(mcp)
