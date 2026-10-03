"""The nightly stats split the Federal Supreme Court by the seat of the deciding division."""
import sqlite3

import generate_stats as gs


def test_social_law_divisions_and_bge_part_v_are_luzern():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE decisions (court TEXT, canton TEXT, docket_number TEXT)")
    rows = [("bger", "8C_862/2008"), ("bger", "9C_12/2020"), ("bger", "I 123/04"), ("bger", "U 45/03"),
            ("bger", "4A_446/2026"), ("bger", "1C_3/2007"), ("bger", "6B_600/2014"), ("bger", "2P.78/2003"),
            ("bge", "BGE 125 V 351"), ("bge", "125 V 351"), ("bge", "BGE 138 III 374"), ("bge", "BGE 136 I 229"),
            ("bvger", "E-1866/2015"), ("ge_gerichte", "8C_1/2020")]
    conn.executemany("INSERT INTO decisions VALUES (?, 'CH', ?)", rows)
    by_court = conn.execute(
        f"SELECT court, canton, COUNT(*) AS count, {gs._LUZERN_SQL} AS luzern FROM decisions GROUP BY court, canton"
    ).fetchall()
    assert gs._federal_seats(by_court) == {"bger": {"LU": 4, "VD": 4}, "bge": {"LU": 2, "VD": 2}}
