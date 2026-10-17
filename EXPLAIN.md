# EXPLAIN.md — Plain-English Walkthrough

A complete technical explainer so you can answer any question about this project confidently.

---

## File-by-File Walkthrough

### `seed.py` — The Data Factory

This file creates all the synthetic data in FalkorDB. It runs once at startup.

**What it does:**
1. Connects to FalkorDB at `localhost:6379` (the Redis port — FalkorDB is Redis-compatible)
2. Clears any existing data with `MATCH (n) DETACH DELETE n`
3. Creates 20 "legitimate" background accounts (noise to make the rings harder to spot)
4. Plants 3 fraud rings:
   - **Ring A:** 4 accounts each with `(a)-[:USED]->(d:Device)` pointing to the *same* Device node `DEV_RING_A`. They form a chain: A1→A2→A3→A4 via `[:SENT]` edges.
   - **Ring B:** 4 accounts each with `(a)-[:LOGGED_FROM]->(ip:IP)` pointing to the *same* IP `IP_RING_B`. They form a circle: B1→B2→B3→B4→B1.
   - **Ring C:** 1 hub account + 5 mule accounts, all connected to `IP_RING_C`. Hub sends to each mule.
5. Creates 3 Alert nodes (one per ring) with metadata about the flagged transaction
6. Creates indexes on Account, Person, Device, IP, Alert for fast lookups

**Key design choice:** Indexes are created with `CREATE INDEX ON :Label(property)`. In FalkorDB, indexes speed up `MATCH (a:Account {id: $id})` queries significantly.

---

### `tools.py` — The Graph Tool Functions

Six functions, each one a specific investigation capability. All return plain Python dicts that can be JSON-serialized.

**Connection management:** `get_graph()` creates a module-level `FalkorDB` client and returns `g = client.select_graph("fraud_graph")`. The `g.query(cypher, params)` method runs a Cypher query and returns a result object with `.result_set` — a list of rows.

#### `linked_accounts(account_id, depth=2)`
Runs 4 separate queries and merges results:
- **Device query:** `MATCH (a)-[:USED]->(d:Device)<-[:USED]-(b)` — finds accounts that used the same device
- **IP query:** `MATCH (a)-[:LOGGED_FROM]->(ip:IP)<-[:LOGGED_FROM]-(b)` — same IP
- **Merchant query:** `MATCH (a)-[:PAID]->(m:Merchant)<-[:PAID]-(b)` — same merchant
- **Transfer query:** `MATCH (a)-[:SENT*1..depth]->(b)` — variable-length path up to `depth` hops

The `*1..2` in Cypher means "1 or 2 hops" — a multi-hop traversal. SQL requires a self-join for each hop level.

#### `trace_path(src, dst)`
Uses FalkorDB's built-in `shortestPath()`:
```cypher
MATCH p = shortestPath((src)-[:SENT*]->(dst))
RETURN [node IN nodes(p) | node.id] AS node_ids, length(p) AS hops
```
`nodes(p)` and `relationships(p)` extract the elements of a matched path. `length(p)` returns the hop count. This is Dijkstra's algorithm running natively inside FalkorDB.

#### `find_circular_flows(account_id, max_length=6)`
```cypher
MATCH (start:Account {id: $acc_id})-[r:SENT*2..max_length]->(start)
```
The key: the path starts AND ends at `start`. FalkorDB's graph engine detects this cycle efficiently using matrix multiplication. The `reduce()` function is Cypher's way of summing a list: `reduce(total = 0.0, rel IN r | total + rel.amount)`.

#### `rank_hubs(top_n=10)`
```cypher
CALL algo.pageRank('Account', 'SENT') YIELD node, score
```
This is a stored procedure call in FalkorDB. `CALL` invokes a built-in algorithm. `YIELD` exposes the outputs. PageRank scores nodes by how many other high-score nodes point to them — hub accounts that many accounts send to will score highest.

#### `find_communities(min_size=2)`
```cypher
CALL algo.wcc() YIELD node, componentId
WITH componentId, collect(node.id) AS members, count(node) AS size
WHERE size >= min_size
RETURN componentId, members, size
```
WCC = Weakly Connected Components. It groups all nodes where you can reach any member from any other member (ignoring edge direction). One fraud ring = one component.

#### `take_action(account_ids, action)`
Writes to FalkorDB. For `freeze`: `SET a.frozen = true`. For `open_case`: creates a `Case` node and `[:FLAGGED_IN]` edges. These are *mutations* — the graph is the system of record.

---

### `agent.py` — The Investigation Loop

Three modes that all produce the same format of output (a stream of step dicts):

**No-LLM mode (`run_nollm_investigation`):**
A fixed sequence: linked_accounts → find_communities → rank_hubs → trace_path → find_circular_flows → take_action → build_report. The order is chosen because each step informs the next (community detection tells us which accounts are in the ring before we rank hubs within it).

**Claude mode (`run_claude_investigation`):**
Uses Anthropic's `tool_use` API. We pass a list of tool schemas and let Claude decide which to call and in what order. The loop: call Claude → if it returns tool_use blocks, execute them and send results back → repeat until Claude returns `end_turn` or a text-only response.

**Gemini mode (`run_gemini_investigation`):**
Uses Gemini's `function_calling` feature. Similar loop but using `google.generativeai` SDK.

**`_build_report()`:**
Assembles the structured report from all tool results. It collects:
- Ring members from the WCC community that contained the flagged account
- Hub from the PageRank results filtered to ring members
- Shared devices/IPs from `linked_accounts` results filtered by link_type
- Circular flows from `find_circular_flows`
- Evidence paths: each claim paired with the Cypher query that produced it

---

### `mcp_server.py` — MCP Exposure

Uses **FastMCP** from the official `mcp` Python SDK. `FastMCP("name")` creates a server. `@mcp.tool()` registers a function as an MCP tool — the SDK reads the function signature and docstring to auto-generate the JSON schema.

Two transport modes:
- **stdio**: MCP client spawns the server as a subprocess, communicates over stdin/stdout (standard for Claude Desktop)
- **SSE**: Server-Sent Events over HTTP (for web-based MCP clients)

Resources are read-only data endpoints: `@mcp.resource("fraud://graph/schema")` exposes the graph schema at that URI.

---

### `app.py` — FastAPI Backend

**SSE streaming:** The `/api/investigate/{alert_id}` endpoint uses a background thread (not async) because `run_investigation()` is a synchronous generator. A `queue.Queue` bridges the thread to the async FastAPI response. Each step is yielded as `data: {json}\n\n` — the Server-Sent Events format.

**Why SSE and not WebSockets?** SSE is simpler (unidirectional, built on HTTP), works through proxies, and doesn't need special client code — just `EventSource` in the browser.

**Report storage:** `_reports` is a module-level dict. For a demo this is fine. In production you'd use Redis or a DB.

---

### `static/index.html` — The UI

**Cytoscape.js** handles the graph visualization. It's loaded from CDN — no build step.

Key functions:
- `initCy(nodes, edges)` — creates the Cytoscape instance with custom style rules
- `animateGraph(tool, result)` — called for each investigation step; adds CSS classes to nodes/edges to highlight them (`.visited`, `.frozen`, `.hub`, `.path-edge`, `.cycle-edge`)
- `handleStep(step, alertId)` — processes each SSE event: adds a step card, calls `animateGraph`, checks if it's the final report
- `renderReport(report)` — builds the structured HTML report from the report object
- The SSE connection is a `ReadableStream` from `fetch()` — works in all modern browsers

---

## Cypher Query Reference

| Query | What It Does | Algorithm |
|-------|-------------|-----------|
| `shortestPath((a)-[:SENT*]->(b))` | Finds shortest transfer path | Dijkstra (BFS) |
| `(a)-[:SENT*2..6]->(a)` | Finds money cycles | DFS with backtracking |
| `CALL algo.pageRank(...)` | Scores accounts by influence | Iterative PageRank |
| `CALL algo.wcc()` | Groups connected accounts | Union-Find |
| `(a)-[:USED]->(d)<-[:USED]-(b)` | Finds shared device | Pattern match |
| `MATCH (n) DETACH DELETE n` | Clear entire graph | N/A |
| `CREATE INDEX ON :Account(id)` | Create property index | B-tree |
| `SET a.frozen = true` | Update node property | N/A |
| `CREATE (c:Case {...})` | Create new node | N/A |
| `CREATE (a)-[:FLAGGED_IN]->(c)` | Create new edge | N/A |

---

## Architecture Decisions

**Why FalkorDB and not Neo4j?**
FalkorDB uses GraphBLAS sparse matrix multiplication for graph algorithms — this is 10–100× faster for analytical workloads. It also runs embedded in Redis, so it doubles as your KV store. For a demo that needs fast results, this matters.

**Why FastAPI and not Flask/Django?**
FastAPI has native async support and excellent streaming response support. SSE streaming in FastAPI is `StreamingResponse` — two lines. Flask needs manual `stream_with_context`. Django needs third-party packages.

**Why Cytoscape.js and not D3 or vis-network?**
Cytoscape.js has the cleanest API for graph-specific operations: programmatic node/edge selection, class-based styling, animated layout transitions. D3's force layout requires much more custom code for the same result.

**Why no-LLM fallback?**
A hackathon demo that requires an API key and breaks if you hit a rate limit is a bad demo. The deterministic pipeline runs the same investigation logic, calls the same tools, produces the same quality report — it just doesn't use an LLM to decide the order. The LLM modes are a bonus.

**Why MCP?**
MCP is becoming the standard protocol for AI tool use. Exposing the tools as MCP makes them interoperable with Claude Desktop, Continue, and any future MCP-compatible client — for free, with no additional integration work.
