//! Blockchain data access for the scanner.
//!
//! - DexScreener public API for recent pairs (primary discovery source)
//! - Optional JSON-RPC account/code probes (Solana / EVM)
//!
//! Does not execute trades or sign transactions.

mod dexscreener;
mod rpc;

pub use dexscreener::{DexScreenerClient, PairSummary};
pub use rpc::{RpcClient, RpcProbe};

use chrono::Utc;
use meme_common::{Chain, Result, TokenCandidate};

/// Discover recent pairs on a chain via DexScreener and map to candidates.
pub async fn discover_candidates(
    dex: &DexScreenerClient,
    chain: Chain,
    max: usize,
) -> Result<Vec<TokenCandidate>> {
    let pairs = dex.recent_pairs(chain, max.saturating_mul(2)).await?;
    let mut out = Vec::new();
    let now = Utc::now();

    for p in pairs {
        // Prefer the base token as the meme candidate
        let address = p
            .base_address
            .clone()
            .or(p.quote_address.clone())
            .unwrap_or_default();
        if address.is_empty() {
            continue;
        }
        // Skip obvious stables / native wrappers lightly
        if is_ignored_symbol(p.base_symbol.as_deref()) {
            continue;
        }
        out.push(TokenCandidate {
            chain,
            address,
            symbol: p.base_symbol,
            name: p.base_name,
            pair_address: p.pair_address,
            dex: p.dex_id,
            liquidity_usd: p.liquidity_usd,
            discovered_at: now,
            source: "dexscreener".into(),
        });
        if out.len() >= max {
            break;
        }
    }
    Ok(out)
}

fn is_ignored_symbol(sym: Option<&str>) -> bool {
    match sym.map(|s| s.to_ascii_uppercase()) {
        Some(s) if matches!(s.as_str(), "USDC" | "USDT" | "DAI" | "WETH" | "ETH" | "SOL" | "WSOL" | "WBTC") => {
            true
        }
        _ => false,
    }
}
