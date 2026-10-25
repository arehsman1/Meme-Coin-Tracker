"""Unit tests for EVM bytecode selector heuristics (no network)."""

from meme_tracker.chains.evm import (
    SELECTOR_MINT,
    SELECTOR_PAUSE,
    SELECTOR_IS_BLACKLISTED,
    ZERO_ADDR,
    _count_delegatecall,
    _decode_address_word,
    _hex_has_selector,
)


def test_selector_detection():
    # mint selector embedded in fake bytecode
    code = "0x60806040" + SELECTOR_MINT.replace("0x", "") + "ff"
    assert _hex_has_selector(code, SELECTOR_MINT)
    assert not _hex_has_selector(code, SELECTOR_PAUSE)


def test_blacklist_selector():
    code = "0x" + SELECTOR_IS_BLACKLISTED.replace("0x", "") + "aabb"
    assert _hex_has_selector(code, SELECTOR_IS_BLACKLISTED)


def test_empty_code():
    assert not _hex_has_selector("0x", SELECTOR_MINT)
    assert not _hex_has_selector("", SELECTOR_MINT)
    assert _count_delegatecall("0x") == 0


def test_delegatecall_count():
    # f4 is DELEGATECALL
    code = "0x00f400f4f4"
    assert _count_delegatecall(code) == 3


def test_decode_address_word():
    word = "0x" + ("0" * 24) + "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    addr = _decode_address_word(word)
    assert addr == "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert _decode_address_word(None) is None
