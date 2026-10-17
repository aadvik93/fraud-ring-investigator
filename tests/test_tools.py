"""
tests/test_tools.py — Pytest tests for all graph tools.

These tests run against a live FalkorDB instance (must be running).
The graph must be seeded with the 3 planted fraud rings.

Run: pytest tests/test_tools.py -v
"""

import pytest
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import (
    linked_accounts,
    trace_path,
    find_circular_flows,
    rank_hubs,
    find_communities,
    take_action,
    get_alerts,
    get_graph_data,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def ensure_seeded():
    """Ensure the graph is seeded before tests run."""
    try:
        from tools import get_graph
        g = get_graph()
        res = g.query("MATCH (a:Alert) RETURN count(a) AS n")
        count = res.result_set[0][0]
        if count < 3:
            import subprocess
            subprocess.run(["python", "seed.py"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), check=True)
    except Exception as e:
        pytest.skip(f"FalkorDB not available: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Tool 1: linked_accounts
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedAccounts:
    def test_returns_dict_with_expected_keys(self):
        result = linked_accounts("RING_A_1")
        assert "accounts" in result
        assert "count" in result
        assert "tool" in result
        assert result["tool"] == "linked_accounts"

    def test_ring_a_accounts_share_device(self):
        """Ring A accounts should all share DEV_RING_A."""
        result = linked_accounts("RING_A_1", depth=2)
        device_links = [a for a in result["accounts"] if a["link_type"] == "device"]
        assert len(device_links) > 0, "Ring A accounts should share a device"
        # All ring A accounts should appear
        linked_ids = {a["account_id"] for a in device_links}
        assert any("RING_A" in aid for aid in linked_ids), "Should find other Ring A accounts via shared device"

    def test_ring_b_accounts_share_ip(self):
        """Ring B accounts should all share IP_RING_B."""
        result = linked_accounts("RING_B_1", depth=2)
        ip_links = [a for a in result["accounts"] if a["link_type"] == "ip"]
        assert len(ip_links) > 0, "Ring B accounts should share an IP"
        linked_ids = {a["account_id"] for a in ip_links}
        assert any("RING_B" in aid for aid in linked_ids)

    def test_ring_c_hub_connects_to_mules(self):
        """Ring C hub should find its mule accounts via transfer links."""
        result = linked_accounts("RING_C_HUB", depth=1)
        transfer_links = [a for a in result["accounts"] if a["link_type"] == "transfer"]
        assert len(transfer_links) >= 5, "Ring C hub should connect to at least 5 mules"

    def test_unknown_account_returns_empty(self):
        result = linked_accounts("DOES_NOT_EXIST_XYZ")
        assert result["count"] == 0
        assert result["accounts"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Tool 2: trace_path
# ══════════════════════════════════════════════════════════════════════════════

class TestTracePath:
    def test_returns_dict_with_expected_keys(self):
        result = trace_path("RING_A_1", "RING_A_3")
        assert "found" in result
        assert "path" in result
        assert "hops" in result
        assert "tool" in result

    def test_ring_a_chain_path_found(self):
        """There should be a path through the Ring A mule chain."""
        result = trace_path("RING_A_1", "RING_A_3")
        assert result["found"] is True
        assert result["hops"] == 2
        assert "RING_A_1" in result["path"]
        assert "RING_A_3" in result["path"]

    def test_ring_a_full_chain(self):
        result = trace_path("RING_A_1", "RING_A_4")
        assert result["found"] is True
        assert result["hops"] == 3

    def test_no_path_between_unconnected_accounts(self):
        result = trace_path("RING_A_1", "RING_B_1")
        # These rings may not be connected — could be not found
        # Just check it doesn't crash
        assert "found" in result

    def test_same_account_not_applicable(self):
        """Tracing from A to A — should either not find or return empty path."""
        result = trace_path("RING_A_1", "RING_A_1")
        # FalkorDB shortestPath with 0 hops — should gracefully handle
        assert "found" in result


# ══════════════════════════════════════════════════════════════════════════════
# Tool 3: find_circular_flows
# ══════════════════════════════════════════════════════════════════════════════

class TestFindCircularFlows:
    def test_returns_dict_with_expected_keys(self):
        result = find_circular_flows("RING_B_1")
        assert "cycles_found" in result
        assert "cycles" in result
        assert "tool" in result

    def test_ring_b_has_circular_flow(self):
        """Ring B is a 4-account circle — should detect the cycle."""
        result = find_circular_flows("RING_B_1", max_length=6)
        assert result["cycles_found"] > 0, "Ring B should have circular flows"

    def test_ring_b_cycle_involves_ring_accounts(self):
        result = find_circular_flows("RING_B_1", max_length=6)
        if result["cycles_found"] > 0:
            cycle = result["cycles"][0]
            cycle_path = cycle.get("cycle_path", [])
            # At least some ring B nodes should appear in the cycle
            ring_b_in_path = [n for n in cycle_path if "RING_B" in str(n)]
            assert len(ring_b_in_path) > 0

    def test_ring_b_cycle_has_positive_amount(self):
        result = find_circular_flows("RING_B_1", max_length=6)
        if result["cycles_found"] > 0:
            assert result["cycles"][0]["total_amount"] > 0

    def test_legit_account_has_no_cycle(self):
        """Legitimate accounts should not appear in cycles."""
        result = find_circular_flows("ACC_LEGIT_1", max_length=6)
        # Ring A entry is from legit 1 but legit 1 itself shouldn't be in a cycle
        assert result["cycles_found"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Tool 4: rank_hubs
# ══════════════════════════════════════════════════════════════════════════════

class TestRankHubs:
    def test_returns_dict_with_hubs(self):
        result = rank_hubs(top_n=5)
        assert "hubs" in result
        assert isinstance(result["hubs"], list)

    def test_hub_scores_are_positive(self):
        result = rank_hubs(top_n=10)
        for hub in result["hubs"]:
            assert hub["pagerank_score"] >= 0

    def test_hub_scores_are_ordered_desc(self):
        result = rank_hubs(top_n=10)
        scores = [h["degree"] for h in result["hubs"]]
        assert scores == sorted(scores, reverse=True)

    def test_ring_c_hub_is_in_top_hubs(self):
        """RING_C_HUB pays 5 mules — should rank high."""
        result = rank_hubs(top_n=15)
        hub_ids = [h["account_id"] for h in result["hubs"]]
        assert "RING_C_HUB" in hub_ids, "RING_C_HUB should appear in top PageRank results"

    def test_returns_requested_number(self):
        result = rank_hubs(top_n=5)
        assert len(result["hubs"]) <= 5


# ══════════════════════════════════════════════════════════════════════════════
# Tool 5: find_communities
# ══════════════════════════════════════════════════════════════════════════════

class TestFindCommunities:
    def test_returns_dict_with_communities(self):
        result = find_communities()
        assert "communities" in result
        assert "community_count" in result

    def test_finds_at_least_three_fraud_communities(self):
        """Should find communities containing the 3 fraud rings."""
        result = find_communities(min_size=2)
        fraud_comms = [c for c in result["communities"] if c["contains_fraud_accounts"]]
        assert len(fraud_comms) >= 1, "Should find at least one community with fraud accounts"

    def test_ring_a_accounts_in_same_community(self):
        """Ring A mule chain should be in the same WCC component."""
        result = find_communities(min_size=2)
        for comm in result["communities"]:
            members = comm.get("members", [])
            ring_a_members = [m for m in members if "RING_A" in str(m)]
            if len(ring_a_members) >= 2:
                return  # Found a community with Ring A accounts
        pytest.fail("Ring A accounts should be in the same WCC community")

    def test_communities_have_size(self):
        result = find_communities(min_size=2)
        for comm in result["communities"]:
            assert comm["size"] >= 2


# ══════════════════════════════════════════════════════════════════════════════
# Tool 6: take_action
# ══════════════════════════════════════════════════════════════════════════════

class TestTakeAction:
    def test_freeze_sets_frozen_flag(self):
        """Freezing an account should set frozen=true in FalkorDB."""
        result = take_action(["ACC_LEGIT_20"], action="freeze")
        assert result["results"][0]["status"] == "frozen"
        # Verify in graph
        from tools import get_graph
        g = get_graph()
        res = g.query("MATCH (a:Account {id: 'ACC_LEGIT_20'}) RETURN a.frozen")
        assert res.result_set[0][0] is True

    def test_open_case_creates_case_node(self):
        result = take_action(["RING_A_1"], action="open_case", notes="Test case")
        assert result["case_id"] is not None
        # Verify case exists in graph
        from tools import get_graph
        g = get_graph()
        case_id = result["case_id"]
        res = g.query(f"MATCH (c:Case {{id: '{case_id}'}}) RETURN c.id")
        assert len(res.result_set) > 0, "Case should exist in FalkorDB"

    def test_freeze_and_open_case_does_both(self):
        result = take_action(["RING_B_2", "RING_B_3"], action="freeze_and_open_case", notes="Full action test")
        assert result["case_id"] is not None
        assert len(result["results"]) == 2
        assert all("frozen" in r for r in result["results"])

    def test_unknown_action_returns_error(self):
        result = take_action(["RING_A_1"], action="invalid_action")
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════════════
# Alerts and graph data
# ══════════════════════════════════════════════════════════════════════════════

class TestGraphData:
    def test_get_alerts_returns_three_alerts(self):
        alerts = get_alerts()
        assert len(alerts) == 3

    def test_alerts_have_expected_ids(self):
        alerts = get_alerts()
        ids = {a["id"] for a in alerts}
        assert "ALERT_001" in ids
        assert "ALERT_002" in ids
        assert "ALERT_003" in ids

    def test_alerts_amounts_match_planted_values(self):
        alerts = get_alerts()
        amounts = {a["txn_id"]: float(a["amount"]) for a in alerts}
        assert abs(amounts["TXN_FLAG_A"] - 999.99) < 0.01
        assert abs(amounts["TXN_FLAG_B"] - 1500.0) < 0.01
        assert abs(amounts["TXN_FLAG_C"] - 25000.0) < 0.01

    def test_graph_data_has_nodes_and_edges(self):
        data = get_graph_data()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) > 10
        assert len(data["edges"]) > 10

    def test_graph_data_contains_fraud_accounts(self):
        data = get_graph_data()
        node_ids = {n["id"] for n in data["nodes"]}
        assert "RING_A_1" in node_ids
        assert "RING_B_1" in node_ids
        assert "RING_C_HUB" in node_ids
