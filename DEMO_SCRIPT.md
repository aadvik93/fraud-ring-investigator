# Demo Script — Fraud Ring Investigator
### Judge-focused 2–3 minute walkthrough

## Before recording
- Start FalkorDB and the app using the README setup instructions.
- Run `python3 seed.py` to reset the synthetic demo data.
- Open `http://localhost:8000`; confirm the graph and three alert cards load.
- Use **No-LLM** mode so the walkthrough is deterministic and does not depend on an API key.
- Keep the report panel visible when possible; do not claim a live deployment unless you have personally opened and verified it.

## 0:00–0:20 — The problem
**Say:** “Fraud can be invisible when every transaction is inspected in isolation. This demo investigates a flagged transfer by following connections among accounts, shared devices and IP addresses, and money movement. All data is synthetic.”

**Show:** the alert list, connected graph, and graph overview metrics.

## 0:20–0:45 — Why the graph matters
**Say:** “FalkorDB is the investigation's data layer, not just a picture. The app queries connected accounts, finds infrastructure-linked communities, ranks transfer hubs, traces shortest money paths, and checks for circular flows. The evidence returned by these graph operations shapes the case.”

**Show:** search for a node, use **Fit graph**, then toggle infrastructure edges off and back on.

## 0:45–1:30 — Alert 002: circular transfers
**Say:** “I’ll investigate Alert 002, which points to a transfer entering a suspected circular-flow cluster. The no-LLM mode makes the walkthrough reproducible. Watch the tool steps and the graph as the workflow checks shared infrastructure, connected components, hub activity, a transfer path, and cycles.”

**Do:** select Alert 002 and click **Investigate**. Let the workflow finish. Show the cycle and evidence paths if present.

## 1:30–2:00 — Explainable decision and action
**Say:** “The report includes an evidence score made from visible graph signals—shared infrastructure, circular flows, transfer degree, and path length. It is a transparent demo triage heuristic, not a trained model or a probability of fraud. The workflow records its action and case in FalkorDB, so the result is more than a visual highlight.”

**Show:** the Graph Evidence Score factors, case ID, affected accounts, and evidence paths. Point out that automatic freezing is gated by the documented score threshold; lower evidence opens a review case without an automatic freeze.

## 2:00–2:30 — A different pattern
**Say:** “Alert 003 demonstrates a different shape: a fan-out hub sending to multiple mule accounts. The same investigation tools can surface a different network structure rather than relying on one cycle pattern.”

**Do:** select Alert 003 and investigate. Show the high-degree account and the shared IP evidence.

## 2:30–2:50 — The graph is writable
**Say:** “The case and account actions are persisted as graph updates. The case node links back to affected accounts, giving an investigator a traceable record of what the workflow did and why.”

**Show:** the case result and, if useful, FalkorDB Browser with the case relationships.

## 2:50–3:00 — Close
**Say:** “Fraud Ring Investigator demonstrates how FalkorDB can give an agent connected context, graph-based evidence, and an auditable action path. The next step toward production would be larger real-world evaluation data, calibrated thresholds, analyst approval policies, and hardened deployment.”

## Questions judges may ask
- **Is the data real?** No. It is synthetic and intentionally includes three planted patterns so the demo is reproducible.
- **Is the score a probability?** No. It is a transparent heuristic with explicit factors and a threshold, not a statistically calibrated fraud probability.
- **Is the no-LLM mode autonomous?** It is a deterministic investigation workflow over graph tools. The optional Claude/Gemini modes are separate and are documented as lightly tested.
- **Why FalkorDB?** Connected-account discovery, weakly connected components, PageRank, shortest paths, and cycle detection are central to the workflow; the case and freeze actions are written back into the graph.
- **Is this production-ready?** No. This is a hackathon prototype with synthetic data. Production use would require calibration, analyst review, audit controls, privacy/security review, and deployment testing.

## Claims to avoid
- Do not claim benchmark speedups without a reproducible benchmark.
- Do not claim the demo proves real-world fraud accuracy.
- Do not describe the deterministic mode as an LLM autonomously deciding every step.
- Do not say the app is publicly deployed unless the live URL has been tested.
