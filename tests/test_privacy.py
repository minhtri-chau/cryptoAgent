"""The user discusses their own positions in conversation; none of that may end up in the research
database. Every write path that takes free text must refuse personal-position phrasing, while
ordinary market language (including other people's positions) still goes through."""

import pytest

from cryptoagent import digest, privacy, research
from cryptoagent.privacy import PrivacyError


@pytest.mark.parametrize("text", [
    "I bought AAVE at $220", "Should I hold my position?", "user's entry was 180", "The user holds SOL",
    "averaging down from my average entry", "my cost basis is 95", "I've added more ETH",
])
def test_personal_phrasing_is_refused(text):
    with pytest.raises(PrivacyError):
        privacy.check(text)


@pytest.mark.parametrize("text", [
    "AAVE fee revenue rose 40% in Q3", "whales added to positions on-chain", "entry at the next open after a breakout",
    "the creator says BTC holds the 200-week average", "holders of the token receive buybacks",
])
def test_market_language_passes(text):
    privacy.check(text)


def test_every_free_text_write_path_is_guarded(conn):
    with pytest.raises(PrivacyError):
        research.add_claim(conn, topic="altcoins", subject="AAVE", statement="I bought AAVE at $220",
                           status="hypothesis", citations=[], confidence=0.3, created_by="agent")
    with pytest.raises(PrivacyError):
        digest.add_entry(conn, "briefing", "agent", "fine", evidence={"note": "user's entry 220"})
    assert conn.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM journal").fetchone()[0] == 0
