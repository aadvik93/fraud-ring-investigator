# NOTES.md — Decisions & Assumptions

## Architecture Decisions

### FalkorDB as Primary Store
Every investigation step either reads from or writes to FalkorDB. Alert nodes, Case nodes, and frozen account flags all live in the graph. This satisfies the hackathon requirement that "the product stops working if FalkorDB is removed" — without the graph, there's no alerts list, no tool results, no reports, no case tracking.

### No-LLM Mode is First-Class
Decided to make the deterministic pipeline a genuine feature, not a fallback. The fixed sequence (linked_accounts → communities → pagerank → trace_path → cycles → action → report) was designed by thinking about what a human analyst would do:
1. First, map who's connected (linked_accounts)
2. Then, find the ring cluster (WCC)
3. Then, identify the nerve center (PageRank)
4. Then, get the evidence trail (shortestPath)
5. Then, confirm the laundering pattern (cycles)
6. Then, act (freeze + case)

This sequence is sensible and produces good reports even without an LLM.

### SSE Over WebSockets
Server-Sent Events chosen over WebSockets because:
- Unidirectional (server → client) is all we need for streaming steps
- Works through all HTTP proxies and CDNs without configuration
- No protocol upgrade needed, no special headers
- The `EventSource` API is natively supported in all browsers

### Cytoscape.js Over vis-network or D3
Cytoscape.js provides the cleanest programmatic API for:
- Adding CSS classes to individual nodes/edges (`.frozen`, `.visited`, `.hub`)
- Animated layout transitions (`cy.animate()`)
- Hover events and tooltips
D3 would require significantly more custom code for equivalent functionality.

### Thread-Pool for Sync Generator in FastAPI
FastAPI is async but `run_investigation()` is a synchronous generator. Used a `threading.Thread` + `queue.Queue` bridge pattern to run the sync generator in a background thread while yielding SSE events to the async FastAPI handler. This avoids blocking the event loop.

### MCP Resources
Added two MCP resources (`fraud://graph/schema` and `fraud://alerts/list`) even though the hackathon only asked for tools. Resources are the MCP equivalent of GET endpoints — they let MCP clients read static or computed data without having to use a tool call. This is better UX for clients like Claude Desktop.

---

## Assumptions

### Synthetic Data Only
All data is synthetic. The 3 fraud ring patterns (mule chain, circular loop, hub/star) are modeled on real fraud patterns described in publicly available financial crime reports, but all names, IDs, amounts, and device fingerprints are fabricated.

### Port Assignments
- `6379` — FalkorDB (Redis protocol)
- `3000` — FalkorDB Browser
- `8000` — FastAPI app
- `8001` — MCP SSE server (optional)

### FalkorDB Algorithm Names
Based on FalkorDB documentation:
- `algo.pageRank(label, rel_type)` — returns `node, score`
- `algo.wcc({nodeLabels: [...], relationshipTypes: [...]})` — returns `node, componentId`
- Or `algo.wcc()` with no args for the whole graph

Tested these against the actual FalkorDB docs at docs.falkordb.com/algorithms.

### Frozen Accounts in Graph
`account.frozen = true` is written directly to FalkorDB. The UI polls `/api/graph` after actions to reflect the new state. In a production system this would trigger a downstream event (Kafka, webhook, etc.), but for the demo the graph is the single source of truth.

### Report Storage
Final reports are stored in `_reports` — a module-level dict in `app.py`. This means reports are lost on server restart. For a hackathon demo this is acceptable. For production: write the report as a `Report` node in FalkorDB.

### LLM Temperature
Not set explicitly — defaults used for both Claude and Gemini. The investigation prompt is structured enough that temperature doesn't significantly affect which tools are called.

### Index Creation
`CREATE INDEX ON :Label(property)` may fail if the index already exists (after re-seeding). This is caught with a try/except and logged as a warning, not an error. FalkorDB handles duplicate index creation gracefully.

### WCC on Full Graph
`algo.wcc()` with no arguments runs on the entire graph. This means legit accounts will appear in communities too — they just won't contain any RING accounts. The code filters for communities containing fraud accounts by checking if any member ID contains "RING". This works for the synthetic dataset; in production you'd use a risk flag on the node.

---

## Trade-offs

| Decision | Trade-off |
|----------|-----------|
| Sync generator + thread bridge | Simple code, not ideal for high concurrency |
| Module-level graph connection | Simple, but not thread-safe for heavy production load |
| No WebSocket | Simpler code, but no bidirectional communication |
| Report in memory | Fast, but lost on restart |
| WCC on full graph | Simple, but scales O(n) with graph size |
| No authentication | Perfect for demo, bad for production |

---

## AI Coding Tool Disclosure

This project was built with AI coding assistance from **Antigravity** (Google DeepMind). 

The AI helped with:
- Writing boilerplate (FastAPI routes, Cytoscape.js initialization)
- Cypher query syntax verification
- FastMCP registration patterns
- Documentation formatting

The author made all architectural decisions, designed the fraud ring patterns, wrote the investigation pipeline logic, and reviewed every generated line. The author can explain every file, every query, and every decision.

**This disclosure is also in README.md** as required by hackathon rules.
