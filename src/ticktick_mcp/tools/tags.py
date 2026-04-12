from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ticktick_mcp.client import TickTickClient


def _get_client(ctx: Context) -> TickTickClient:
    return ctx.request_context.lifespan_context["client"]  # type: ignore[union-attr]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_tags(ctx: Context) -> list[dict[str, Any]]:
        """List all tags.

        Returns all tags with their names, colors, parent relationships, and sort settings.
        Requires v2 session token.
        """
        client = _get_client(ctx)
        return await client.v2_get("/tags")
