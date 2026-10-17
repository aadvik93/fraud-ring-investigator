# Demo Script — Fraud Ring Investigator
### 2–3 Minute Video Walkthrough

---

## Pre-demo checklist (do before recording)
- [ ] `docker compose up` is running, app is at http://localhost:8000
- [ ] Browser is on http://localhost:8000 in full screen, dark mode OS
- [ ] DevTools closed, zoom at 100%
- [ ] Three alert cards are visible on the left panel
- [ ] Graph is loaded with nodes visible

---

## Script

### [0:00 – 0:20] Hook — Problem Statement

> **Say:** "Financial fraud costs the global economy over \$5 trillion a year. The problem isn't a lack of data — it's that fraud lives in *relationships*. A fraudster isn't caught by looking at one account. They're caught by seeing who they're connected to, what devices they share, and how money flows in circles.

> Relational databases were not built for this. But graphs were."

*[Show the UI — no clicks yet. Just let the graph render.]*

---

### [0:20 – 0:45] Architecture Overview

> **Say:** "This is the Fraud Ring Investigator. On the left, flagged transactions. In the center, the live investigation graph powered by FalkorDB. On the right, the AI agent's reasoning steps and final report.

> The database is FalkorDB — a high-performance graph database built on top of Redis, using sparse matrix multiplication for native graph algorithms. Remove FalkorDB, and nothing works."

*[Point to the graph legend — Ring A purple, Ring B pink, Ring C teal.]*

---

### [0:45 – 1:30] Investigate Ring B — The Circular Loop

> **Say:** "Let's start with Alert 002 — a \$1,500 transfer into a circular money laundering ring."

*[Click on ALERT_002 card on the left. The flagged account highlights on the graph.]*

> **Say:** "I'll run the agent in No-LLM mode — a deterministic pipeline that always works. But you can also plug in Claude or Gemini."

*[Click the **Investigate** button.]*

> **Say:** "Watch the graph animate as the agent works. First it calls `linked_accounts` — mapping every account connected to the flagged one. Then `find_communities` runs Weakly Connected Components to find the fraud ring cluster. Then PageRank finds the hub."

*[Pause — watch the nodes turn green as they're visited, community highlights animate.]*

> **Say:** "Now — the critical step. `find_circular_flows` detects a money cycle. Money went out and came back. This is classic laundering. The Cypher query is 4 lines. In SQL, you'd need a recursive CTE with cycle guards — 40+ lines that degrades exponentially."

*[Click the SQL toggle at the bottom of the graph to show the Cypher vs SQL comparison.]*

---

### [1:30 – 2:00] Actions & Evidence

> **Say:** "The agent doesn't just observe — it acts. It calls `take_action` with `freeze_and_open_case`. Watch the accounts turn red — frozen in FalkorDB right now."

*[Watch Ring B accounts turn red (frozen) on the graph.]*

> **Say:** "A Case node is created in the graph, linked to every account in the ring via `[:FLAGGED_IN]` edges. This isn't a visualization — it's written to the database."

*[Click the **Final Report** tab on the right.]*

> **Say:** "The report shows: the ring members, the hub account, shared IP, the circular money flow total, case ID, frozen accounts — and crucially — the evidence path for every claim. Every conclusion has a graph path behind it. This is explainable AI."

---

### [2:00 – 2:30] Ring C — Hub/Star + PageRank

*[Click ALERT_003, then Investigate.]*

> **Say:** "Ring C is different — a star pattern. One hub account distributes to 5 mules, all sharing the same IP. PageRank surfaces RING_C_HUB as the top-scoring account in the entire graph. That's not a coincidence — it routes money for everyone."

*[Watch hub account get a larger golden highlight.]*

> **Say:** "25,000 dollars entered this hub from a seemingly legitimate account. The agent traces the path, identifies the ring, freezes all 6 accounts, and opens a case — all in under 10 seconds."

---

### [2:30 – 2:50] MCP & Developer Story

> **Say:** "For developers: all 6 graph tools are also exposed as an MCP server. Any MCP client — Claude Desktop, Continue IDE — can call these tools directly and investigate fraud interactively."

*[Briefly show terminal with: `python mcp_server.py`]*

---

### [2:50 – 3:00] Close

> **Say:** "Fraud rings hide in relationships. FalkorDB makes those relationships a first-class citizen. The graph doesn't just store the data — it IS the intelligence."

*[End on the Final Report tab with frozen accounts glowing red on the graph.]*

---

## Key talking points (if asked questions)

- **"Why FalkorDB over Neo4j?"** — FalkorDB uses GraphBLAS sparse linear algebra for algorithms, runs in-process with Redis, and is 10–100× faster on analytical queries. It's also fully open source.
- **"Could this be done in SQL?"** — Technically yes, with recursive CTEs and external graph libraries. But every query would be a project. In FalkorDB, they're one-liners.
- **"Why MCP?"** — MCP lets any AI assistant use these tools without custom integration. Claude Desktop users get fraud investigation as a built-in capability.
- **"Is the data real?"** — All synthetic. Three planted fraud rings: mule chain, circular loop, hub/star. Classic real-world patterns.
