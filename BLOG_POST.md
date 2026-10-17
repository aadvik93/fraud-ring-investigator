# Building a Graph-Powered Fraud Ring Investigator with FalkorDB

*Or: why I stopped asking "which accounts are suspicious?" and started asking "how are they connected?"*

---

When I started this project for the WeMakeDevs × FalkorDB Graph Hacks hackathon, I had a simple premise: financial fraud doesn't live in individual transactions. It lives in *relationships*. A mule account looks completely ordinary until you see it shares a device fingerprint with four other accounts, all of which route money in a circle back to a hub that just received \$25,000 from an unsuspecting victim.

That story cannot be told in a relational database without significant pain. It can be told in a graph in four lines of Cypher.

## The Data Model That Makes It Click

The first design decision was the graph model. I settled on seven node types:

```
(Person)-[:OWNS]->(Account)
(Account)-[:USED]->(Device)
(Account)-[:LOGGED_FROM]->(IP)
(Account)-[:PAID]->(Merchant)
(Account)-[:SENT {amount, ts}]->(Account)
(Account)-[:FLAGGED_IN]->(Case)
```

Each relationship carries real semantics. `[:SENT]` has an amount and timestamp. `[:USED]` connects an account to a device fingerprint. `[:LOGGED_FROM]` connects it to an IP. These aren't just metadata fields in a relational table — they're first-class edges in the graph, traversable in any direction, joinable at query time with no foreign key ceremony.

I planted three synthetic fraud rings, each representing a classic real-world pattern:
- **Ring A (Mule Chain):** Four accounts in a linear chain, all sharing a single device fingerprint.
- **Ring B (Circular Loop):** Four accounts sending money B1→B2→B3→B4→B1, all using the same IP.
- **Ring C (Hub/Star):** One central hub distributing to five mules, the hub identifiable by PageRank.

## The Moment FalkorDB Made Me a Believer

The "aha" moment came when I wrote the circular flow detection query:

```cypher
MATCH (start:Account {id: $acc_id})-[r:SENT*2..6]->(start)
RETURN [rel IN r | startNode(rel).id] AS cycle_nodes,
       reduce(total = 0.0, rel IN r | total + rel.amount) AS total_amount
```

Four lines. It finds every cycle of length 2 to 6 starting from a given account, returns the path, and totals the money involved. FalkorDB executes this using sparse matrix multiplication — GraphBLAS under the hood — in milliseconds.

The equivalent SQL is a recursive Common Table Expression with cycle-detection guards (you need to track the visited path as an array and break when you revisit a node), roughly 40 lines that degrades exponentially with graph size. And SQL gives you no native PageRank or WCC — you'd need an external Python library and a data export step.

## The Agent Architecture

The investigation pipeline is a tool-calling loop. Six tools, each backed by a FalkorDB Cypher query or algorithm call:

1. **`linked_accounts`** — multi-hop traversal to map connected accounts
2. **`trace_path`** — `shortestPath()` to build an evidence trail
3. **`find_circular_flows`** — cycle detection for laundering patterns  
4. **`rank_hubs`** — `algo.pageRank()` to find nerve centers
5. **`find_communities`** — `algo.wcc()` to cluster fraud rings
6. **`take_action`** — write freezes and Case nodes back to FalkorDB

The agent supports three modes: Claude (native tool_use), Gemini (function calling), and a deterministic no-LLM pipeline that runs the same sequence without an API key. The demo always works.

I also wrapped all six tools as an MCP server using FastMCP. Any MCP client — Claude Desktop, Continue IDE — can now call these tools directly and ask questions like "what's the fraud ring around account RING_B_1?" in natural language.

## The Report as Evidence

The hardest part wasn't the graph queries. It was the report. A fraud investigator submitting a case to compliance can't just say "trust me, this is a ring." They need evidence paths: account A is connected to account B through shared device X, and the money flows in a cycle with total \$1,200.

Every claim in the report links to the Cypher query that produced it. The cycle path is shown as a node sequence. The hub account's PageRank score is included. The case ID created in FalkorDB is shown. This is what explainable AI looks like in practice — not a probability score, but a traceable chain of reasoning.

## What I Learned

**Graphs are not for everything.** Aggregations like "total spend per merchant" are fine in SQL. But "find all accounts that share infrastructure with a known fraudster and appear in a money cycle" is a graph query, full stop.

**FalkorDB's native algorithms are the killer feature.** You don't pipe your data to Python, run NetworkX, and pipe it back. You call `algo.pageRank()` or `algo.wcc()` in Cypher and it runs inside the database engine, with sparse linear algebra optimizations. For a demo that needed to work in real-time, this mattered.

**MCP is quietly becoming infrastructure.** Adding an MCP server took about 50 lines with FastMCP. The payoff is that every tool I built is now accessible to any AI assistant that speaks MCP — for free, forever, without custom integration work.

## Try It

```bash
docker compose up
open http://localhost:8000
```

Pick an alert, click Investigate, and watch the graph light up as the agent traces the fraud ring in real time.

The code is on GitHub. The graph does real work. The agent takes real action. And the report shows its work.

---

*Built for the WeMakeDevs × FalkorDB Graph Hacks Hackathon, Track 01: Agents That Act on Connected Data.*
