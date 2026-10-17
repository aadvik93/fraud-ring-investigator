"""
seed.py — Synthetic fraud-ring data loader for FalkorDB.

Plants exactly 3 fraud rings:
  Ring A: mule chain   — 4 accounts share ONE device; linear A1->A2->A3->A4
  Ring B: circular     — 4 accounts share ONE IP; money loops B1->B2->B3->B4->B1
  Ring C: star/hub     — 1 hub pays 5 mules; all share ONE IP

Run: python seed.py
"""

import os
import random
import time
from falkordb import FalkorDB

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", 6379))
GRAPH_NAME = "fraud_graph"

def get_db():
    db = FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT)
    return db.select_graph(GRAPH_NAME)

def clear_graph(g):
    g.query("MATCH (n) DETACH DELETE n")
    print("✓ Graph cleared")

def seed(g):
    # ── Legit background users (noise) ──────────────────────────────────────
    for i in range(1, 21):
        g.query(f"""
            CREATE (p:Person {{id: 'P_LEGIT_{i}', name: 'LegitUser{i}', risk: 'low'}})
            CREATE (a:Account {{id: 'ACC_LEGIT_{i}', balance: {random.randint(500,10000)}, frozen: false, risk: 'low'}})
            CREATE (p)-[:OWNS]->(a)
        """)

    # Add some legit transactions
    for i in range(1, 20):
        amt = round(random.uniform(10, 500), 2)
        ts = int(time.time()) - random.randint(0, 86400 * 30)
        g.query(f"""
            MATCH (a:Account {{id: 'ACC_LEGIT_{i}'}}), (b:Account {{id: 'ACC_LEGIT_{i+1}'}})
            CREATE (a)-[:SENT {{amount: {amt}, ts: {ts}, txn_id: 'TXN_LEGIT_{i}'}}]->(b)
        """)

    print("✓ Legit background nodes created")

    # ── RING A: Mule Chain ───────────────────────────────────────────────────
    # Create ONE shared device first, then link each account to it
    g.query("CREATE (d:Device {id: 'DEV_RING_A', fingerprint: 'fp_ring_a_shared', type: 'mobile'})")
    for i in range(1, 5):
        g.query(f"""
            CREATE (p:Person {{id: 'P_RING_A_{i}', name: 'RingAMule{i}', risk: 'high'}})
            CREATE (a:Account {{id: 'RING_A_{i}', balance: {random.randint(100,1000)}, frozen: false, risk: 'high'}})
            CREATE (p)-[:OWNS]->(a)
        """)
        # Link each account to the SINGLE shared device
        g.query(f"""
            MATCH (a:Account {{id: 'RING_A_{i}'}}), (d:Device {{id: 'DEV_RING_A'}})
            CREATE (a)-[:USED]->(d)
        """)

    # Chain transfers A1->A2->A3->A4
    for i in range(1, 4):
        amt = round(500 - i * 50, 2)
        ts = int(time.time()) - (4 - i) * 3600
        g.query(f"""
            MATCH (a:Account {{id: 'RING_A_{i}'}}), (b:Account {{id: 'RING_A_{i+1}'}})
            CREATE (a)-[:SENT {{amount: {amt}, ts: {ts}, txn_id: 'TXN_A_{i}_{i+1}'}}]->(b)
        """)

    # Entry: flagged transaction from legit account into ring
    g.query(f"""
        MATCH (a:Account {{id: 'ACC_LEGIT_1'}}), (b:Account {{id: 'RING_A_1'}})\n        CREATE (a)-[:SENT {{amount: 999.99, ts: {int(time.time()) - 7200}, txn_id: 'TXN_FLAG_A', flagged: true}}]->(b)
    """)

    # Shared merchant for ring A
    g.query("CREATE (m:Merchant {id: 'MERCH_A', name: 'ShadyShop A', category: 'crypto'})")
    for i in range(1, 5):
        g.query(f"""
            MATCH (a:Account {{id: 'RING_A_{i}'}}), (m:Merchant {{id: 'MERCH_A'}})
            CREATE (a)-[:PAID {{amount: 50, ts: {int(time.time())}}}]->(m)
        """)

    print("✓ Ring A (mule chain) seeded")

    # ── RING B: Circular Money Loop ──────────────────────────────────────────
    # Create ONE shared IP first, then link each account to it
    g.query("CREATE (ip:IP {id: 'IP_RING_B', address: '192.168.99.1', country: 'XX'})")
    for i in range(1, 5):
        g.query(f"""
            CREATE (p:Person {{id: 'P_RING_B_{i}', name: 'RingBMule{i}', risk: 'high'}})
            CREATE (a:Account {{id: 'RING_B_{i}', balance: {random.randint(100,1000)}, frozen: false, risk: 'high'}})
            CREATE (p)-[:OWNS]->(a)
        """)
        g.query(f"""
            MATCH (a:Account {{id: 'RING_B_{i}'}}), (ip:IP {{id: 'IP_RING_B'}})
            CREATE (a)-[:LOGGED_FROM]->(ip)
        """)

    # Circular chain: B1->B2->B3->B4->B1
    circle = [(1,2),(2,3),(3,4),(4,1)]
    for (src, dst) in circle:
        amt = round(300 + random.uniform(-20, 20), 2)
        ts = int(time.time()) - random.randint(100, 3600)
        g.query(f"""
            MATCH (a:Account {{id: 'RING_B_{src}'}}), (b:Account {{id: 'RING_B_{dst}'}})
            CREATE (a)-[:SENT {{amount: {amt}, ts: {ts}, txn_id: 'TXN_B_{src}_{dst}'}}]->(b)
        """)

    # Flagged entry into ring B
    g.query(f"""
        MATCH (a:Account {{id: 'ACC_LEGIT_5'}}), (b:Account {{id: 'RING_B_1'}})
        CREATE (a)-[:SENT {{amount: 1500.0, ts: {int(time.time()) - 5000}, txn_id: 'TXN_FLAG_B', flagged: true}}]->(b)
    """)

    print("✓ Ring B (circular) seeded")

    # ── RING C: Hub/Star ─────────────────────────────────────────────────────
    # Create ONE shared IP, then link hub + mules to it
    g.query("CREATE (ip:IP {id: 'IP_RING_C', address: '10.0.99.1', country: 'YY'})")
    g.query("""
        CREATE (p:Person {id: 'P_RING_C_HUB', name: 'RingCHub', risk: 'critical'})
        CREATE (a:Account {id: 'RING_C_HUB', balance: 50000, frozen: false, risk: 'critical'})
        CREATE (p)-[:OWNS]->(a)
    """)
    g.query("""
        MATCH (a:Account {id: 'RING_C_HUB'}), (ip:IP {id: 'IP_RING_C'})
        CREATE (a)-[:LOGGED_FROM]->(ip)
    """)

    for i in range(1, 6):
        g.query(f"""
            CREATE (p:Person {{id: 'P_RING_C_{i}', name: 'RingCMule{i}', risk: 'high'}})
            CREATE (a:Account {{id: 'RING_C_{i}', balance: {random.randint(100,2000)}, frozen: false, risk: 'high'}})
            CREATE (p)-[:OWNS]->(a)
        """)
        g.query(f"""
            MATCH (a:Account {{id: 'RING_C_{i}'}}), (ip:IP {{id: 'IP_RING_C'}})
            CREATE (a)-[:LOGGED_FROM]->(ip)
        """)
        amt = round(2000 + random.uniform(-100, 100), 2)
        ts = int(time.time()) - random.randint(100, 7200)
        g.query(f"""
            MATCH (hub:Account {{id: 'RING_C_HUB'}}), (mule:Account {{id: 'RING_C_{i}'}})
            CREATE (hub)-[:SENT {{amount: {amt}, ts: {ts}, txn_id: 'TXN_C_HUB_{i}'}}]->(mule)
        """)

    # Flagged entry into ring C hub
    g.query(f"""
        MATCH (a:Account {{id: 'ACC_LEGIT_10'}}), (b:Account {{id: 'RING_C_HUB'}})
        CREATE (a)-[:SENT {{amount: 25000.0, ts: {int(time.time()) - 1800}, txn_id: 'TXN_FLAG_C', flagged: true}}]->(b)
    """)

    print("✓ Ring C (hub/star) seeded")

    # ── Flagged transactions / alerts ─────────────────────────────────────────
    alerts = [
        ("ALERT_001", "TXN_FLAG_A", 999.99,   "ACC_LEGIT_1",  "RING_A_1",   "Unusual amount, recipient linked to mule chain",           "Ring A - Mule Chain"),
        ("ALERT_002", "TXN_FLAG_B", 1500.0,   "ACC_LEGIT_5",  "RING_B_1",   "Destination account in circular transfer cluster",         "Ring B - Circular Loop"),
        ("ALERT_003", "TXN_FLAG_C", 25000.0,  "ACC_LEGIT_10", "RING_C_HUB", "Large transfer to known hub account",                      "Ring C - Hub/Star"),
    ]
    for (aid, txn, amt, frm, to, reason, ring) in alerts:
        g.query(f"""
            CREATE (alert:Alert {{
                id: '{aid}', txn_id: '{txn}', amount: {amt},
                from_acc: '{frm}', to_acc: '{to}',
                reason: '{reason}', ring: '{ring}', status: 'open'
            }})
        """)

    print("✓ Alert nodes seeded")

    # ── Indexes for fast lookup ───────────────────────────────────────────────
    for label, prop in [("Account","id"),("Person","id"),("Device","id"),("IP","id"),("Alert","id"),("Alert","txn_id")]:
        try:
            g.query(f"CREATE INDEX ON :{label}({prop})")
        except Exception as e:
            print(f"  Index warning ({label}.{prop}): {e}")

    print("✓ Indexes created")
    print(f"\n🎉 Seed complete — 3 fraud rings planted in graph '{GRAPH_NAME}'")

def verify(g):
    """Quick sanity check after seeding."""
    res = g.query("MATCH (a:Account) RETURN count(a) AS n")
    print(f"  Accounts:         {res.result_set[0][0]}")
    res = g.query("MATCH ()-[r:SENT]->() RETURN count(r) AS n")
    print(f"  SENT edges:       {res.result_set[0][0]}")
    res = g.query("MATCH (a:Alert) RETURN count(a) AS n")
    print(f"  Alerts:           {res.result_set[0][0]}")
    res = g.query("MATCH (d:Device) RETURN count(d) AS n")
    print(f"  Device nodes:     {res.result_set[0][0]}  (should be 1)")
    res = g.query("MATCH (ip:IP) RETURN count(ip) AS n")
    print(f"  IP nodes:         {res.result_set[0][0]}  (should be 2)")
    res = g.query("MATCH (a:Account)-[:USED]->(d:Device) RETURN count(a) AS n")
    print(f"  Account->Device:  {res.result_set[0][0]}  (should be 4)")

if __name__ == "__main__":
    print(f"Connecting to FalkorDB at {FALKORDB_HOST}:{FALKORDB_PORT}...")
    g = get_db()
    clear_graph(g)
    seed(g)
    print("\n── Verification ──")
    verify(g)
