"""Public settings surface — independent toggles stay OFF by default."""

from meme_tracker.settings_public import build_public_settings


def test_public_settings_trading_off():
    data = build_public_settings()
    assert data["live_trading"] is False
    assert data["auto_trading_enabled"] is False
    assert data["automatic_buying"] is False
    assert data["automatic_selling"] is False
    assert data["wallet_signing"] is False
    assert data["wallet_connection_enabled"] is False
    assert data["public_wallet_tracking_enabled"] is True
    assert data["significant_swap_usd_threshold_default"] == 500.0
    assert "solana" in data["supported_chains"]
    assert data["safety_notes"]
    assert any("personal wallet" in n.lower() for n in data["safety_notes"])
