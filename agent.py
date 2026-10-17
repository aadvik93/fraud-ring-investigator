"""
agent.py — Fraud Ring Investigator Agent

Supports three modes:
  1. ANTHROPIC_API_KEY set: Claude with native tool_use
  2. GEMINI_API_KEY set:    Gemini with function calling
  3. Neither set:           Deterministic "no-LLM" pipeline (always works)

The agent takes a flagged transaction (alert), calls graph tools in a loop,
and produces a structured final report with evidence paths.

Usage:
  python agent.py --alert ALERT_001
  python agent.py --alert ALERT_001 --mode nollm
"""

import os
import json
import time
import argparse
import logging
from typing import Generator, Optional

from tools import (
    linked_accounts,
    trace_path,
    find_circular_flows,
    rank_hubs,
    find_communities,
    take_action,
    get_alerts,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tool registry — maps tool names to functions and their schemas
# ══════════════════════════════════════════════════════════════════════════════

TOOL_REGISTRY = {
    "linked_accounts": {
        "fn": linked_accounts,
        "description": (
            "Find all accounts linked to a given account via shared Device, IP, "
            "or Merchant, and via transfer hops. Returns account IDs and link types."
        ),
        "parameters": {
            "account_id": {"type": "string", "description": "Account ID to start from"},
            "depth": {"type": "integer", "description": "Transfer hop depth (default 2)"}
        },
        "required": ["account_id"]
    },
    "trace_path": {
        "fn": trace_path,
        "description": (
            "Find the shortest transfer path between two accounts. "
            "Returns the path nodes, hop count, and edge details as evidence."
        ),
        "parameters": {
            "src_account": {"type": "string", "description": "Source account ID"},
            "dst_account": {"type": "string", "description": "Destination account ID"}
        },
        "required": ["src_account", "dst_account"]
    },
    "find_circular_flows": {
        "fn": find_circular_flows,
        "description": (
            "Detect circular money flows (cycles) starting and ending at an account. "
            "Cycles indicate money laundering — funds sent out and returned."
        ),
        "parameters": {
            "account_id": {"type": "string", "description": "Account ID to check for cycles"},
            "max_length": {"type": "integer", "description": "Maximum cycle length (default 6)"}
        },
        "required": ["account_id"]
    },
    "rank_hubs": {
        "fn": rank_hubs,
        "description": (
            "Run PageRank to identify the most influential hub accounts — "
            "those that many others route money through. Hub accounts are "
            "the nerve centers of fraud rings."
        ),
        "parameters": {
            "top_n": {"type": "integer", "description": "Number of top hubs to return (default 10)"}
        },
        "required": []
    },
    "find_communities": {
        "fn": find_communities,
        "description": (
            "Run Weakly Connected Components (WCC) to find clusters of connected accounts. "
            "Each cluster containing flagged accounts is a candidate fraud ring."
        ),
        "parameters": {
            "min_size": {"type": "integer", "description": "Minimum cluster size to include (default 2)"}
        },
        "required": []
    },
    "take_action": {
        "fn": take_action,
        "description": (
            "Take compliance action: 'freeze' accounts, 'open_case', or "
            "'freeze_and_open_case'. Actions are written back to FalkorDB. "
            "Returns case_id and list of affected accounts."
        ),
        "parameters": {
            "account_ids": {"type": "array", "items": {"type": "string"}, "description": "List of account IDs"},
            "action": {"type": "string", "enum": ["freeze", "open_case", "freeze_and_open_case"]},
            "notes": {"type": "string", "description": "Optional investigation notes"}
        },
        "required": ["account_ids", "action"]
    }
}


def dispatch_tool(name: str, args: dict) -> dict:
    """Call a tool by name with given arguments."""
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return {"error": f"Unknown tool: {name}"}
    try:
        return entry["fn"](**args)
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# No-LLM deterministic investigation pipeline
# ══════════════════════════════════════════════════════════════════════════════

def run_nollm_investigation(alert: dict) -> Generator[dict, None, None]:
    """
    Fixed investigation pipeline that always finds the fraud ring
    without needing an LLM. Steps mirror what a trained analyst would do.

    Yields step dicts that the FastAPI endpoint streams as SSE events.
    """
    to_acc = alert["to_acc"]
    from_acc = alert["from_acc"]
    ring_name = alert.get("ring", "Unknown Ring")

    yield _step("start", "Investigation started", {
        "alert_id": alert["id"],
        "txn_id": alert["txn_id"],
        "amount": alert["amount"],
        "from": from_acc,
        "to": to_acc,
        "reason": alert["reason"]
    })

    # Step 1: Find linked accounts
    r1 = dispatch_tool("linked_accounts", {"account_id": to_acc, "depth": 2})
    yield _step("linked_accounts", "Mapping connected accounts", r1)

    linked = [a["account_id"] for a in r1.get("accounts", [])]
    all_ring_accounts = list(set([to_acc] + linked))

    # Step 2: Find communities to identify the ring
    r2 = dispatch_tool("find_communities", {"min_size": 2})
    yield _step("find_communities", "Running WCC community detection", r2)

    # Identify which community the flagged account belongs to
    ring_community = []
    for comm in r2.get("communities", []):
        if to_acc in comm.get("members", []):
            ring_community = [m for m in comm["members"] if not str(m).startswith(("IP_", "DEV_"))]
            break

    # Step 3: Rank hubs — find the most influential account
    r3 = dispatch_tool("rank_hubs", {"top_n": 60})
    r3["hubs"] = sorted(r3.get("hubs", []), key=lambda h: (-h.get("degree", 0), h["account_id"]))
    yield _step("rank_hubs", "Running PageRank to find hub accounts", r3)

    # Find the hub within the ring community
    hub_account = None
    for hub in r3.get("hubs", []):
        if hub["account_id"] in ring_community:
            hub_account = hub["account_id"]
            break
    if not hub_account and ring_community:
        hub_account = ring_community[0]

    # Step 4: Trace path from entry to hub
    if hub_account and hub_account != to_acc:
        r4 = dispatch_tool("trace_path", {"src_account": to_acc, "dst_account": hub_account})
    else:
        r4 = dispatch_tool("trace_path", {"src_account": from_acc, "dst_account": to_acc})
    yield _step("trace_path", "Tracing money flow path", r4)

    # Step 5: Find circular flows
    check_acc = hub_account or to_acc
    r5 = dispatch_tool("find_circular_flows", {"account_id": check_acc, "max_length": 6})
    yield _step("find_circular_flows", "Detecting circular money flows", r5)

    # Also check the ring B account if no cycles found yet
    if r5.get("cycles_found", 0) == 0 and to_acc != check_acc:
        r5b = dispatch_tool("find_circular_flows", {"account_id": to_acc, "max_length": 6})
        if r5b.get("cycles_found", 0) > 0:
            r5 = r5b
            yield _step("find_circular_flows", "Found circular flows at entry account", r5)

    # Step 6: Take action — freeze and open case
    freeze_accounts = list(set(ring_community or all_ring_accounts))[:10]  # cap at 10
    notes = f"Automated investigation of {ring_name}. Triggered by alert {alert['id']}."
    r6 = dispatch_tool("take_action", {
        "account_ids": freeze_accounts,
        "action": "freeze_and_open_case",
        "notes": notes
    })
    yield _step("take_action", "Freezing accounts and opening compliance case", r6)

    # Step 7: Build final report
    report = _build_report(alert, r1, r2, r3, r4, r5, r6, ring_community, hub_account)
    yield _step("report", "Investigation complete — final report generated", report)


def _step(tool: str, summary: str, result: dict) -> dict:
    return {
        "type": "step",
        "tool": tool,
        "summary": summary,
        "result": result,
        "ts": int(time.time())
    }


def _build_report(alert, r_linked, r_communities, r_hubs, r_path, r_cycles, r_action,
                  ring_community, hub_account) -> dict:
    """Assemble the structured final report."""
    case_id = r_action.get("case_id", "N/A")
    frozen = [x["account_id"] for x in r_action.get("results", [])]

    # Shared devices/IPs from linked_accounts
    devices = [a for a in r_linked.get("accounts", []) if a.get("link_type") == "device"]
    ips = [a for a in r_linked.get("accounts", []) if a.get("link_type") == "ip"]

    # Circular flows summary
    circles = r_cycles.get("cycles", [])
    circular_summary = []
    for c in circles:
        circular_summary.append({
            "path": " → ".join(c.get("cycle_path", [])),
            "total_amount": c.get("total_amount", 0)
        })

    # Evidence paths
    evidence = []
    if r_path.get("found"):
        evidence.append({
            "claim": f"Money flows from {r_path['path'][0]} to {r_path['path'][-1]} through the ring",
            "path": " → ".join(r_path.get("path", [])),
            "hops": r_path.get("hops", 0),
            "cypher": f"MATCH (a:Account {{id:'{r_path['path'][0]}'}}), (b:Account {{id:'{r_path['path'][-1]}'}}) WITH shortestPath((a)-[:SENT*]->(b)) AS p RETURN p"
        })
    if circles:
        evidence.append({
            "claim": "Circular money flows detected (laundering pattern)",
            "path": " → ".join(circles[0].get("cycle_path", [])),
            "cypher": "MATCH (a:Account)-[:SENT*2..6]->(a) RETURN a.id"
        })
    if devices:
        evidence.append({
            "claim": f"Accounts share device fingerprint",
            "accounts": [d["account_id"] for d in devices],
            "cypher": "MATCH (a:Account)-[:USED]->(d:Device)<-[:USED]-(b:Account) RETURN a.id, b.id, d.id"
        })
    if ips:
        evidence.append({
            "claim": f"Accounts share IP address",
            "accounts": [i["account_id"] for i in ips],
            "cypher": "MATCH (a:Account)-[:LOGGED_FROM]->(ip:IP)<-[:LOGGED_FROM]-(b:Account) RETURN a.id, b.id, ip.address"
        })

    return {
        "type": "report",
        "summary": (
            f"Fraud ring identified: {alert.get('ring', 'Unknown')}. "
            f"{len(ring_community)} accounts involved. "
            f"Hub: {hub_account}. "
            f"Case {case_id} opened, {len(frozen)} accounts frozen."
        ),
        "alert": alert,
        "ring_name": alert.get("ring", "Unknown"),
        "ring_members": ring_community,
        "hub_account": hub_account,
        "shared_devices": sorted({d["shared_resource"] for d in devices if d.get("shared_resource")}) if devices else [],
        "shared_ips": sorted({i["shared_resource"] for i in ips if i.get("shared_resource")}) if ips else [],
        "circular_flows": circular_summary,
        "actions_taken": {
            "case_id": case_id,
            "frozen_accounts": frozen
        },
        "evidence_paths": evidence,
        "pagerank_top": ([h for h in r_hubs.get("hubs", []) if h["account_id"] in ring_community][:5] or r_hubs.get("hubs", [])[:5]),
        "community_size": len(ring_community)
    }


# ══════════════════════════════════════════════════════════════════════════════
# Claude (Anthropic) LLM mode
# ══════════════════════════════════════════════════════════════════════════════

def run_claude_investigation(alert: dict) -> Generator[dict, None, None]:
    """Run investigation using Claude's native tool_use."""
    try:
        import anthropic
    except ImportError:
        yield _step("error", "anthropic package not installed", {})
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build Claude tool schemas
    claude_tools = []
    for name, entry in TOOL_REGISTRY.items():
        props = {}
        for param, spec in entry["parameters"].items():
            props[param] = {
                "type": spec["type"],
                "description": spec.get("description", "")
            }
            if "enum" in spec:
                props[param]["enum"] = spec["enum"]
            if "items" in spec:
                props[param]["items"] = spec["items"]
        claude_tools.append({
            "name": name,
            "description": entry["description"],
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": entry["required"]
            }
        })

    system_prompt = """You are a fraud ring investigator using a graph database (FalkorDB).
You have access to graph tools to investigate flagged transactions.
Investigate the alert systematically:
1. Use linked_accounts to find connected accounts
2. Use find_communities to identify the fraud ring cluster  
3. Use rank_hubs to find the central hub account (PageRank)
4. Use trace_path to get the money trail as evidence
5. Use find_circular_flows to detect laundering patterns
6. Use take_action with 'freeze_and_open_case' to act on the ring

Always provide evidence paths in your analysis. Be concise but complete."""

    messages = [
        {
            "role": "user",
            "content": f"Investigate this flagged transaction:\n{json.dumps(alert, indent=2)}\n\nUse all available tools to identify the fraud ring, then take action (freeze accounts and open a case)."
        }
    ]

    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=system_prompt,
            tools=claude_tools,
            messages=messages
        )

        # Check if Claude wants to use tools
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text"]

        if text_blocks:
            yield _step("llm_thought", "Agent reasoning", {"text": text_blocks[0].text})

        if not tool_calls:
            # Final response
            final_text = " ".join(b.text for b in text_blocks if hasattr(b, "text"))
            yield _step("report", "Claude investigation complete", {"summary": final_text})
            break

        # Execute tool calls
        tool_results = []
        for tc in tool_calls:
            result = dispatch_tool(tc.name, tc.input)
            yield _step(tc.name, f"Tool: {tc.name}", result)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps(result)
            })

        # Append assistant message and tool results
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        if response.stop_reason == "end_turn":
            break


# ══════════════════════════════════════════════════════════════════════════════
# Gemini LLM mode
# ══════════════════════════════════════════════════════════════════════════════

def run_gemini_investigation(alert: dict) -> Generator[dict, None, None]:
    """Run investigation using Gemini function calling."""
    try:
        import google.generativeai as genai
    except ImportError:
        yield _step("error", "google-generativeai package not installed", {})
        return

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    # Build Gemini tool schemas
    from google.generativeai.types import FunctionDeclaration, Tool

    declarations = []
    for name, entry in TOOL_REGISTRY.items():
        props = {}
        for param, spec in entry["parameters"].items():
            ptype = spec["type"]
            gemini_type = {
                "string": "STRING", "integer": "INTEGER",
                "array": "ARRAY", "boolean": "BOOLEAN"
            }.get(ptype, "STRING")
            prop_def = {"type": gemini_type, "description": spec.get("description", "")}
            if ptype == "array":
                prop_def["items"] = {"type": "STRING"}
            props[param] = prop_def

        declarations.append(FunctionDeclaration(
            name=name,
            description=entry["description"],
            parameters={"type": "OBJECT", "properties": props, "required": entry["required"]}
        ))

    tools = [Tool(function_declarations=declarations)]
    model = genai.GenerativeModel("gemini-1.5-pro", tools=tools)

    prompt = f"""You are a fraud ring investigator. Investigate this alert using graph tools:

{json.dumps(alert, indent=2)}

Steps: 1) linked_accounts 2) find_communities 3) rank_hubs 4) trace_path 5) find_circular_flows 6) take_action (freeze_and_open_case).
Provide evidence for every claim."""

    chat = model.start_chat()
    response = chat.send_message(prompt)

    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        candidate = response.candidates[0]
        part = candidate.content.parts[0]

        if hasattr(part, "function_call") and part.function_call.name:
            fc = part.function_call
            args = dict(fc.args)
            result = dispatch_tool(fc.name, args)
            yield _step(fc.name, f"Tool: {fc.name}", result)

            # Send tool result back
            from google.generativeai.types import content_types
            tool_response = content_types.to_content({
                "parts": [{
                    "function_response": {
                        "name": fc.name,
                        "response": {"result": result}
                    }
                }]
            })
            response = chat.send_message(tool_response)
        else:
            # Text response — done
            text = part.text if hasattr(part, "text") else "Investigation complete"
            yield _step("report", "Gemini investigation complete", {"summary": text})
            break


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_investigation(alert_id: str, mode: str = "auto") -> Generator[dict, None, None]:
    """
    Main dispatcher. Chooses LLM backend based on env vars or mode flag.
    Always falls back to no-LLM mode if API keys are missing.
    """
    # Look up alert
    alerts = get_alerts()
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if not alert:
        yield _step("error", f"Alert {alert_id} not found", {})
        return

    if mode == "nollm":
        yield from run_nollm_investigation(alert)
    elif mode == "claude" or (mode == "auto" and os.getenv("ANTHROPIC_API_KEY")):
        yield from run_claude_investigation(alert)
    elif mode == "gemini" or (mode == "auto" and os.getenv("GEMINI_API_KEY")):
        yield from run_gemini_investigation(alert)
    else:
        logger.info("No LLM API key found — running deterministic no-LLM pipeline")
        yield from run_nollm_investigation(alert)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fraud Ring Investigator Agent")
    parser.add_argument("--alert", default="ALERT_001", help="Alert ID to investigate")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "nollm", "claude", "gemini"],
                        help="Agent mode")
    args = parser.parse_args()

    print(f"\n🔍 Investigating alert: {args.alert} | mode: {args.mode}\n{'='*60}")
    for step in run_investigation(args.alert, args.mode):
        tool = step.get("tool", "?")
        summary = step.get("summary", "")
        print(f"\n[{tool.upper()}] {summary}")
        if step.get("result"):
            result_str = json.dumps(step["result"], indent=2, default=str)
            # Truncate long outputs for CLI readability
            if len(result_str) > 800:
                result_str = result_str[:800] + "\n... (truncated)"
            print(result_str)
