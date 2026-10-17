# Fraud Ring Investigator 🕵️

> **Track 01: Agents That Act on Connected Data**
> Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB), Oct 2026

An agent that takes a flagged bank transaction, investigates it by querying a **FalkorDB graph**, identifies the fraud ring behind it, freezes the accounts, opens a compliance case (written back into the graph), and produces a report where every claim is backed by a graph path.

FalkorDB is central: every investigation step is a graph query or a native graph algorithm, and the actions (freeze, open case) are written back as graph updates. Data is **fully synthetic**.

## What it does
1. Pick a flagged alert in the web UI and click **Investigate**.
2. The agent calls graph tools in sequence: linked accounts, communities, hub ranking, shortest path, cycle detection.
3. It freezes the ring's accounts and creates a `Case` node linked to each of them.
4. It outputs a report with ring members, hub account, shared device/IP, circular flows, actions taken, and the Cypher behind each claim. The graph view highlights the accounts involved.

The default mode (**No-LLM**) runs a deterministic pipeline over the same tools, so the demo works without any API key. An LLM mode (Claude or Gemini via API key) is implemented but is optional and lightly tested.

## Architecture
```mermaid
flowchart TD
    UI["Web UI (vanilla JS + Cytoscape.js)"] --> API["FastAPI (app.py)"]
    API --> AGENT["Agent (agent.py)"]
    AGENT --> TOOLS["Graph tools (tools.py)"]
    MCP["MCP server (mcp_server.py)"] --> TOOLS
    TOOLS -- "Cypher + graph algorithms" --> DB[("FalkorDB")]
```

## Graph data model
Three planted rings: **A** (mule chain, shared device), **B** (circular money loop, shared IP), **C** (hub sending to 5 mules, shared IP), hidden among 20 legitimate accounts.

## Key queries and algorithms (as used in `tools.py`)

**Shortest money path** (evidence trail; `shortestPath` must be in `WITH`/`RETURN` in FalkorDB):
```cypher
MATCH (src:Account {id: $src}), (dst:Account {id: $dst})
WITH shortestPath((src)-[:SENT*]->(dst)) AS p
RETURN [n IN nodes(p) | n.id] AS node_ids, length(p) AS hops
```

**Circular flows** (laundering pattern):
```cypher
MATCH p = (start:Account {id: $acc_id})-[:SENT*2..6]->(start)
RETURN [n IN nodes(p) | n.id] AS cycle_nodes,
       reduce(total = 0.0, rel IN relationships(p) | total + rel.amount) AS total_amount
```

**Ring detection: weakly connected components over shared devices/IPs:**
```cypher
CALL algo.WCC({nodeLabels: ['Account','Device','IP'],
               relationshipTypes: ['USED','LOGGED_FROM']})
YIELD node, componentId
WITH componentId, collect(node.id) AS members, count(node) AS size
WHERE size >= 2
RETURN componentId, members, size ORDER BY size DESC
```

**Hub ranking: PageRank plus transfer degree.** PageRank favors accounts that *receive* money, so degree is added to catch fan-out hubs that *send* to many mules:
```cypher
CALL algo.pageRank('Account', 'SENT') YIELD node, score
MATCH (node)-[r:SENT]-()
RETURN node.id AS account_id, score, count(r) AS degree
ORDER BY degree DESC, score DESC
```

**Shared infrastructure:** multi-hop patterns such as
`(a:Account)-[:USED]->(d:Device)<-[:USED]-(b:Account)`, and the same for `IP` and `Merchant`.

**Actions written back to the graph:** `SET a.frozen = true` and `CREATE (c:Case)` with `(a)-[:FLAGGED_IN]->(c)`.

## Why a graph
Finding rings through shared devices and IPs, tracing multi-hop money paths, and detecting cycles are all pattern problems over relationships. In a relational database each of these typically needs several self-joins or recursive queries; here they are short Cypher patterns plus built-in algorithms (PageRank, WCC, shortestPath).

## Setup (tested on macOS)
Prerequisites: Docker, Python 3.11+.
```bash
# 1. Start FalkorDB
docker run -d --name falkordb_test -p 6379:6379 -p 3000:3000 falkordb/falkordb:latest

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed the synthetic graph
python3 seed.py

# 4. Start the app
python3 -m uvicorn app:app --port 8000
```
Open **http://localhost:8000**. FalkorDB Browser is at http://localhost:3000. Use **Reset Demo** in the UI to re-seed.

Optional LLM mode: copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`.

## Tests
```bash
python3 -m pytest -q     # requires FalkorDB running and the graph seeded
```
43 tests cover the six graph tools, the graph data endpoint and the no-LLM investigation.

## MCP server
`mcp_server.py` exposes the same six tools (`linked_accounts`, `trace_path`, `find_circular_flows`, `rank_hubs`, `find_communities`, `take_action`) through the Model Context Protocol. It is implemented but not covered by the automated tests.

## Known limitations
- Docker Compose for the whole app and the Render deployment files are included but untested; use the setup above.
- LLM mode is optional; the verified path is the deterministic no-LLM pipeline.
- Data is synthetic and small (about 34 accounts); this is a demo, not a production system.

## AI tools disclosure
This project was built with AI assistance: **Antigravity (Google Gemini models)** generated the initial scaffold and most files, and **Claude (Anthropic)** helped with planning, debugging, and fixing the graph queries and tests. The author ran, tested, and fixed the project, and is responsible for it.

## Project structure
