"""
mcp_server.py — MCP (Model Context Protocol) server for Fraud Ring Investigator.

Exposes all 6 graph tools via the official MCP Python SDK (MCPServer).
Any MCP client (Claude Desktop, Continue, etc.) can connect and use these tools.

Run: python mcp_server.py
  or: python mcp_server.py --transport sse

MCP Inspector: npx @modelcontextprotocol/inspector python mcp_server.py
"""

import json
from mcp.server.mcpserver import MCPServer

from tools import (
    linked_accounts as _linked_accounts,
    trace_path as _trace_path,
    find_circular_flows as _find_circular_flows,
    rank_hubs as _rank_hubs,
    find_communities as _find_communities,
    take_action as _take_action,
)

# ── Create MCP server ─────────────────────────────────────────────────────────
mcp = MCPServer(
    name="fraud-ring-investigator",
    description=(
        "Graph-powered fraud ring investigation tools backed by FalkorDB. "
        "Use these tools to investigate flagged transactions, find fraud rings, "
        "trace money flows, detect circular laundering, and take compliance actions."
    )
)


# ══════════════════════════════════════════════════════════════════════════════
# Tool registrations
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def linked_accounts(account_id: str, depth: int = 2) -> str:
    """
    Find all accounts linked to a given account via shared Device, IP, or Merchant,
    and via transfer hops up to `depth` levels deep.
    Returns account IDs and how they are linked (device/ip/merchant/transfer).
    """
    result = _linked_accounts(account_id=account_id, depth=depth)
    return json.dumps(result, default=str)


@mcp.tool()
def trace_path(src_account: str, dst_account: str) -> str:
    """
    Find the shortest transfer path between two accounts using FalkorDB shortestPath.
    Returns the ordered list of account IDs and transaction details on the path.
    Use this to build an evidence trail for a money-flow claim.
    """
    result = _trace_path(src_account=src_account, dst_account=dst_account)
    return json.dumps(result, default=str)


@mcp.tool()
def find_circular_flows(account_id: str, max_length: int = 6) -> str:
    """
    Detect circular money flows starting and ending at an account.
    Circular flows indicate money laundering — funds sent out and returned through
    multiple hops to obscure the original source.
    Returns all detected cycles with total amounts.
    """
    result = _find_circular_flows(account_id=account_id, max_length=max_length)
    return json.dumps(result, default=str)


@mcp.tool()
def rank_hubs(top_n: int = 10) -> str:
    """
    Run PageRank over the Account transfer graph to rank the most influential hub accounts.
    Hub accounts route money for many others and are the nerve centers of fraud rings.
    Uses FalkorDB's native algo.pageRank algorithm.
    """
    result = _rank_hubs(top_n=top_n)
    return json.dumps(result, default=str)


@mcp.tool()
def find_communities(min_size: int = 2) -> str:
    """
    Run Weakly Connected Components (WCC) to discover clusters of connected accounts.
    Each cluster containing flagged accounts is a candidate fraud ring.
    Uses FalkorDB's native algo.wcc algorithm.
    """
    result = _find_communities(min_size=min_size)
    return json.dumps(result, default=str)


@mcp.tool()
def take_action(account_ids: list, action: str, notes: str = "") -> str:
    """
    Take a compliance action on one or more accounts.
    Actions: 'freeze' (freeze accounts), 'open_case' (create case node in graph),
    'freeze_and_open_case' (both).
    These actions are written back to FalkorDB — the graph does real work.
    Returns the case ID and list of affected accounts.
    """
    result = _take_action(account_ids=account_ids, action=action, notes=notes)
    return json.dumps(result, default=str)


# ══════════════════════════════════════════════════════════════════════════════
# Resources (read-only graph info for MCP clients)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.resource("fraud://graph/schema")
def graph_schema() -> str:
    """Return the FalkorDB graph data model."""
    return json.dumps({
        "graph_name": "fraud_graph",
        "nodes": [
            {"label": "Person", "properties": ["id", "name", "risk"]},
            {"label": "Account", "properties": ["id", "balance", "frozen", "risk"]},
            {"label": "Device", "properties": ["id", "fingerprint", "type"]},
            {"label": "IP", "properties": ["id", "address", "country"]},
            {"label": "Merchant", "properties": ["id", "name", "category"]},
            {"label": "Case", "properties": ["id", "opened_at", "notes", "status"]},
            {"label": "Alert", "properties": ["id", "txn_id", "amount", "from_acc", "to_acc", "reason", "status"]}
        ],
        "relationships": [
            {"type": "OWNS", "from": "Person", "to": "Account"},
            {"type": "USED", "from": "Account", "to": "Device"},
            {"type": "LOGGED_FROM", "from": "Account", "to": "IP"},
            {"type": "PAID", "from": "Account", "to": "Merchant"},
            {"type": "SENT", "from": "Account", "to": "Account", "properties": ["amount", "ts", "txn_id", "flagged"]},
            {"type": "FLAGGED_IN", "from": "Account", "to": "Case"}
        ]
    }, indent=2)


@mcp.resource("fraud://alerts/list")
def list_alerts() -> str:
    """Return all open fraud alerts."""
    from tools import get_alerts
    return json.dumps(get_alerts(), default=str)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fraud Ring MCP Server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"],
                        help="MCP transport (stdio for clients, sse for HTTP)")
    args = parser.parse_args()

    print(f"Starting Fraud Ring MCP Server (transport: {args.transport})")
    mcp.run(transport=args.transport)
