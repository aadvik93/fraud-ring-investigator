"""
tools.py — Graph tool functions backed by FalkorDB.

Each function queries the fraud_graph and returns a plain Python dict
so it can be serialized by FastAPI, used by the agent, and exposed via MCP.

Tools:
  linked_accounts(account_id, depth)   — find accounts sharing device/IP/merchant
  trace_path(src, dst)                 — shortest transfer path
  find_circular_flows(account_id)      — detect money cycles
  rank_hubs(top_n)                     — PageRank to find influential accounts
  find_communities()                   — WCC community detection
  take_action(account_ids, action)     — freeze accounts / open case
"""

import os
import time
import uuid
from falkordb import FalkorDB

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", 6379))
GRAPH_NAME = "fraud_graph"

# ── DB connection (module-level, lazily created) ───────────────────────────────
_db_client = None
_graph = None

def get_graph():
    """Return the FalkorDB graph handle, reconnecting if necessary."""
    global _db_client, _graph
    try:
        if _graph is None:
            _db_client = FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT)
            _graph = _db_client.select_graph(GRAPH_NAME)
        return _graph
    except Exception:
        _db_client = FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT)
        _graph = _db_client.select_graph(GRAPH_NAME)
        return _graph


# ══════════════════════════════════════════════════════════════════════════════
# Tool 1 — Linked Accounts
# ══════════════════════════════════════════════════════════════════════════════

def linked_accounts(account_id: str, depth: int = 2) -> dict:
    """
    Find all accounts linked to `account_id` via shared Device, IP, Merchant,
    or transfer hops within `depth` hops.

    Returns accounts that share infrastructure (same device fingerprint, IP, or
    merchant), which is a strong fraud signal.

    Cypher: multi-hop pattern match across USED/LOGGED_FROM/PAID/SENT relationships.
    """
    g = get_graph()

    # Shared device — accounts that used the SAME device node
    q_device = """
        MATCH (a:Account {id: $acc_id})-[:USED]->(d:Device)<-[:USED]-(b:Account)
        WHERE b.id <> $acc_id
        RETURN b.id AS account_id, b.risk AS risk, 'device' AS link_type,
               d.fingerprint AS shared_resource
    """

    # Shared IP — accounts that logged from the same IP node
    q_ip = """
        MATCH (a:Account {id: $acc_id})-[:LOGGED_FROM]->(ip:IP)<-[:LOGGED_FROM]-(b:Account)
        WHERE b.id <> $acc_id
        RETURN b.id AS account_id, b.risk AS risk, 'ip' AS link_type,
               ip.address AS shared_resource
    """

    # Shared merchant — accounts that paid the same merchant
    q_merchant = """
        MATCH (a:Account {id: $acc_id})-[:PAID]->(m:Merchant)<-[:PAID]-(b:Account)
        WHERE b.id <> $acc_id
        RETURN b.id AS account_id, b.risk AS risk, 'merchant' AS link_type,
               m.name AS shared_resource
    """

    # Transfer neighbors — variable-length SENT path (outbound and inbound)
    q_transfer_out = """
        MATCH (a:Account {id: $acc_id})-[:SENT*1..%d]->(b:Account)
        WHERE b.id <> $acc_id
        RETURN DISTINCT b.id AS account_id, b.risk AS risk, 'transfer' AS link_type,
               null AS shared_resource
    """ % depth

    q_transfer_in = """
        MATCH (b:Account)-[:SENT*1..%d]->(a:Account {id: $acc_id})
        WHERE b.id <> $acc_id
        RETURN DISTINCT b.id AS account_id, b.risk AS risk, 'transfer' AS link_type,
               null AS shared_resource
    """ % depth

    results = []
    seen = set()
    for q in [q_device, q_ip, q_merchant, q_transfer_out, q_transfer_in]:
        rows = g.query(q, {"acc_id": account_id}).result_set
        for row in rows:
            aid = row[0]
            if aid and (aid, row[2]) not in seen:
                seen.add((aid, row[2]))
                results.append({
                    "account_id": aid,
                    "risk": row[1],
                    "link_type": row[2],
                    "shared_resource": row[3]
                })

    return {
        "tool": "linked_accounts",
        "input": {"account_id": account_id, "depth": depth},
        "count": len(results),
        "accounts": results
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tool 2 — Trace Path
# ══════════════════════════════════════════════════════════════════════════════

def trace_path(src_account: str, dst_account: str) -> dict:
    """Shortest transfer path between two accounts (the evidence trail).
    shortestPath() must be used inside WITH or RETURN in FalkorDB."""
    g = get_graph()
    q = """
        MATCH (src:Account {id: $src}), (dst:Account {id: $dst})
        WITH shortestPath((src)-[:SENT*]->(dst)) AS p
        RETURN [n IN nodes(p) | n.id] AS node_ids,
               length(p) AS hops,
               [r IN relationships(p) | {txn_id: r.txn_id, amount: r.amount, ts: r.ts}] AS edges
    """
    rows = g.query(q, {"src": src_account, "dst": dst_account}).result_set
    if not rows or not rows[0][0]:
        return {"tool": "trace_path", "input": {"src": src_account, "dst": dst_account},
                "found": False, "path": [], "hops": -1, "edges": []}
    row = rows[0]
    return {"tool": "trace_path", "input": {"src": src_account, "dst": dst_account},
            "found": True, "path": row[0], "hops": row[1] if row[1] is not None else -1,
            "edges": row[2] or []}


# ══════════════════════════════════════════════════════════════════════════════
# Tool 3 — Find Circular Flows
# ══════════════════════════════════════════════════════════════════════════════

def find_circular_flows(account_id: str, max_length: int = 6) -> dict:
    """Detect money cycles that start and end at account_id (layering pattern).
    A SQL version would need a recursive CTE with cycle detection."""
    g = get_graph()
    q = """
        MATCH p = (start:Account {id: $acc_id})-[:SENT*2..%d]->(start)
        RETURN [n IN nodes(p) | n.id] AS cycle_nodes,
               reduce(total = 0.0, rel IN relationships(p) | total + rel.amount) AS total_amount,
               [rel IN relationships(p) | {txn_id: rel.txn_id, amount: rel.amount}] AS edges
        LIMIT 10
    """ % max_length
    rows = g.query(q, {"acc_id": account_id}).result_set
    cycles = []
    for row in rows:
        cycles.append({"cycle_path": list(row[0]) if row[0] else [],
                       "total_amount": round(float(row[1] or 0), 2),
                       "edges": row[2] or []})
    return {"tool": "find_circular_flows",
            "input": {"account_id": account_id, "max_length": max_length},
            "cycles_found": len(cycles), "cycles": cycles}


# ══════════════════════════════════════════════════════════════════════════════
# Tool 4 — Rank Hubs (PageRank)
# ══════════════════════════════════════════════════════════════════════════════

def rank_hubs(top_n: int = 10) -> dict:
    """PageRank over the SENT network, combined with transfer degree.
    PageRank rewards accounts that RECEIVE money; degree catches fan-out hubs
    that SEND to many mules. Sorted by degree first, then PageRank."""
    g = get_graph()
    q = """
        CALL algo.pageRank('Account', 'SENT')
        YIELD node, score
        MATCH (node)-[r:SENT]-()
        RETURN node.id AS account_id, node.risk AS risk, score, count(r) AS degree
        ORDER BY degree DESC, score DESC
        LIMIT $top_n
    """
    rows = g.query(q, {"top_n": top_n}).result_set
    hubs = [{"account_id": r[0], "risk": r[1], "pagerank_score": round(float(r[2]), 6),
             "degree": r[3]} for r in rows]
    return {"tool": "rank_hubs", "input": {"top_n": top_n}, "hubs": hubs}


# ══════════════════════════════════════════════════════════════════════════════
# Tool 5 — Find Communities (WCC)
# ══════════════════════════════════════════════════════════════════════════════

def find_communities(min_size: int = 2) -> dict:
    """Weakly Connected Components over shared devices and IPs.
    Each cluster of accounts sharing infrastructure is a candidate fraud ring."""
    g = get_graph()
    q = """
        CALL algo.WCC({nodeLabels: ['Account', 'Device', 'IP'],
                       relationshipTypes: ['USED', 'LOGGED_FROM']})
        YIELD node, componentId
        WITH componentId, collect(node.id) AS members, count(node) AS size
        WHERE size >= $min_size
        RETURN componentId, members, size
        ORDER BY size DESC
        LIMIT 20
    """
    rows = g.query(q, {"min_size": min_size}).result_set
    communities = []
    for row in rows:
        members = row[1] if row[1] else []
        communities.append({"component_id": row[0], "size": row[2], "members": members,
                            "contains_fraud_accounts": any("RING" in str(m) for m in members)})
    return {"tool": "find_communities", "input": {"min_size": min_size},
            "community_count": len(communities), "communities": communities}


# ══════════════════════════════════════════════════════════════════════════════
# Tool 6 — Take Action
# ══════════════════════════════════════════════════════════════════════════════

def take_action(account_ids: list, action: str, notes: str = "") -> dict:
    """
    Take a compliance action on one or more accounts:
      - 'freeze'              : set account.frozen = true (turns red in UI)
      - 'open_case'           : create a Case node linked to all affected accounts
      - 'freeze_and_open_case': do both

    These actions are WRITTEN BACK to FalkorDB — demonstrating the graph
    does real work (not just visualization).
    """
    g = get_graph()

    results = []
    case_id = None

    if action == "freeze":
        for acc_id in account_ids:
            g.query("MATCH (a:Account {id: $id}) SET a.frozen = true", {"id": acc_id})
            results.append({"account_id": acc_id, "status": "frozen", "frozen": True})

    elif action == "open_case":
        case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"
        ts = int(time.time())
        g.query(
            "CREATE (c:Case {id: $cid, opened_at: $ts, notes: $notes, status: 'open'})",
            {"cid": case_id, "ts": ts, "notes": notes}
        )
        for acc_id in account_ids:
            g.query(
                "MATCH (a:Account {id: $acc_id}), (c:Case {id: $cid}) CREATE (a)-[:FLAGGED_IN]->(c)",
                {"acc_id": acc_id, "cid": case_id}
            )
            results.append({"account_id": acc_id, "linked_to_case": case_id})

    elif action == "freeze_and_open_case":
        case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"
        ts = int(time.time())
        g.query(
            "CREATE (c:Case {id: $cid, opened_at: $ts, notes: $notes, status: 'open'})",
            {"cid": case_id, "ts": ts, "notes": notes}
        )
        for acc_id in account_ids:
            g.query("MATCH (a:Account {id: $id}) SET a.frozen = true", {"id": acc_id})
            g.query(
                "MATCH (a:Account {id: $acc_id}), (c:Case {id: $cid}) CREATE (a)-[:FLAGGED_IN]->(c)",
                {"acc_id": acc_id, "cid": case_id}
            )
            results.append({"account_id": acc_id, "status": "frozen", "frozen": True, "linked_to_case": case_id})

    else:
        return {"tool": "take_action", "error": f"Unknown action: {action}"}

    return {
        "tool": "take_action",
        "input": {"account_ids": account_ids, "action": action},
        "case_id": case_id,
        "results": results,
        "total_accounts_affected": len(results)
    }


# ── Convenience: full graph data for the visualizer ───────────────────────────

def get_graph_data() -> dict:
    """Return all nodes and edges for the front-end graph visualizer."""
    g = get_graph()

    node_queries = [
        ("Account",  "MATCH (a:Account)  RETURN a.id AS id, a.risk AS risk, a.frozen AS frozen, a.balance AS balance, 'Account' AS label"),
        ("Person",   "MATCH (p:Person)   RETURN p.id AS id, p.risk AS risk, false AS frozen, null AS balance, 'Person' AS label"),
        ("Device",   "MATCH (d:Device)   RETURN d.id AS id, null AS risk, false AS frozen, null AS balance, 'Device' AS label"),
        ("IP",       "MATCH (ip:IP)      RETURN ip.id AS id, null AS risk, false AS frozen, null AS balance, 'IP' AS label"),
        ("Merchant", "MATCH (m:Merchant) RETURN m.id AS id, null AS risk, false AS frozen, null AS balance, 'Merchant' AS label"),
    ]

    nodes = []
    node_ids = set()
    for label, q in node_queries:
        for row in g.query(q).result_set:
            nid = str(row[0]) if row[0] is not None else None
            if nid and nid not in node_ids:
                node_ids.add(nid)
                nodes.append({
                    "id": nid,
                    "label": label,
                    "risk": row[1],
                    "frozen": bool(row[2]) if row[2] else False,
                    "balance": row[3]
                })

    edge_queries = [
        "MATCH (a)-[r:SENT]->(b) RETURN a.id AS src, b.id AS dst, 'SENT' AS type, r.amount AS amount, r.txn_id AS txn_id, r.flagged AS flagged",
        "MATCH (a)-[r:OWNS]->(b) RETURN a.id AS src, b.id AS dst, 'OWNS' AS type, null AS amount, null AS txn_id, null AS flagged",
        "MATCH (a)-[r:USED]->(b) RETURN a.id AS src, b.id AS dst, 'USED' AS type, null AS amount, null AS txn_id, null AS flagged",
        "MATCH (a)-[r:LOGGED_FROM]->(b) RETURN a.id AS src, b.id AS dst, 'LOGGED_FROM' AS type, null AS amount, null AS txn_id, null AS flagged",
        "MATCH (a)-[r:PAID]->(b) RETURN a.id AS src, b.id AS dst, 'PAID' AS type, null AS amount, null AS txn_id, null AS flagged",
        "MATCH (a)-[r:FLAGGED_IN]->(b) RETURN a.id AS src, b.id AS dst, 'FLAGGED_IN' AS type, null AS amount, null AS txn_id, null AS flagged",
    ]

    edges = []
    for q in edge_queries:
        for row in g.query(q).result_set:
            src = str(row[0]) if row[0] is not None else None
            dst = str(row[1]) if row[1] is not None else None
            if src and dst:
                edges.append({
                    "source": src,
                    "target": dst,
                    "type": row[2],
                    "amount": row[3],
                    "txn_id": row[4],
                    "flagged": bool(row[5]) if row[5] else False
                })

    return {"nodes": nodes, "edges": edges}


def get_alerts() -> list:
    """Return all open alerts from the graph."""
    g = get_graph()
    q = """
        MATCH (a:Alert)
        RETURN a.id, a.txn_id, a.amount, a.from_acc, a.to_acc, a.reason, a.ring, a.status
        ORDER BY a.amount DESC
    """
    rows = g.query(q).result_set
    return [
        {
            "id": row[0],
            "txn_id": row[1],
            "amount": row[2],
            "from_acc": row[3],
            "to_acc": row[4],
            "reason": row[5],
            "ring": row[6],
            "status": row[7]
        }
        for row in rows
    ]
