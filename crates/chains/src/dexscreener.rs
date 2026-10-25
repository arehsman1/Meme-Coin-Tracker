//! DexScreener public HTTP client (no API key required for basic endpoints).

use meme_common::{Chain, Result, ScannerError};
use reqwest::Client;
use serde::Deserialize;
use tracing::{debug, warn};

#[derive(Debug, Clone)]
pub struct DexScreenerClient {
    http: Client,
    base_url: String,
}

#[derive(Debug, Clone, Default)]
pub struct PairSummary {
    pub pair_address: Option<String>,
    pub dex_id: Option<String>,
    pub base_address: Option<String>,
    pub base_symbol: Option<String>,
    pub base_name: Option<String>,
    pub quote_address: Option<String>,
    pub liquidity_usd: Option<f64>,
    pub txns_h24_buys: Option<u64>,
    pub txns_h24_sells: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct SearchResponse {
    pairs: Option<Vec<RawPair>>,
}

#[derive(Debug, Deserialize)]
struct TokenPairsResponse {
    // token endpoint returns array at top level sometimes; we also handle object
    #[serde(default)]
    pairs: Option<Vec<RawPair>>,
}

#[derive(Debug, Deserialize)]
struct RawPair {
    #[serde(rename = "pairAddress")]
    pair_address: Option<String>,
    #[serde(rename = "dexId")]
    dex_id: Option<String>,
    #[serde(rename = "chainId")]
    chain_id: Option<String>,
    #[serde(rename = "baseToken")]
    base_token: Option<RawToken>,
    #[serde(rename = "quoteToken")]
    quote_token: Option<RawToken>,
    liquidity: Option<RawLiquidity>,
    txns: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
struct RawToken {
    address: Option<String>,
    symbol: Option<String>,
    name: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawLiquidity {
    usd: Option<f64>,
}

impl DexScreenerClient {
    pub fn new() -> Self {
        Self {
            http: Client::builder()
                .user_agent("meme-coin-tracker-scanner/0.1")
                .timeout(std::time::Duration::from_secs(15))
                .build()
                .expect("reqwest client"),
            base_url: "https://api.dexscreener.com".into(),
        }
    }

    /// Search recent pairs for a chain. Uses public search endpoint filtered by chainId.
    pub async fn recent_pairs(&self, chain: Chain, limit: usize) -> Result<Vec<PairSummary>> {
        // Public search — query chain-related terms; filter client-side by chainId
        let q = match chain {
            Chain::Solana => "SOL",
            Chain::Ethereum => "ETH",
            Chain::Base => "BASE",
        };
        let url = format!("{}/latest/dex/search", self.base_url);
        debug!(chain = %chain, q, "dexscreener search");

        let resp = self
            .http
            .get(&url)
            .query(&[("q", q)])
            .send()
            .await
            .map_err(|e| ScannerError::Http(e.to_string()))?;

        if !resp.status().is_success() {
            return Err(ScannerError::Http(format!("status {}", resp.status())));
        }

        let body: SearchResponse = resp
            .json()
            .await
            .map_err(|e| ScannerError::Parse(e.to_string()))?;

        let chain_id = chain.dexscreener_id();
        let mut out = Vec::new();
        for p in body.pairs.unwrap_or_default() {
            if p.chain_id.as_deref() != Some(chain_id) {
                continue;
            }
            out.push(map_pair(p));
            if out.len() >= limit {
                break;
            }
        }
        if out.is_empty() {
            warn!(chain = %chain, "dexscreener returned no pairs for chain filter");
        }
        Ok(out)
    }

    pub async fn token_pairs(&self, chain: Chain, address: &str) -> Result<Vec<PairSummary>> {
        let url = format!(
            "{}/token-pairs/v1/{}/{}",
            self.base_url,
            chain.dexscreener_id(),
            address
        );
        let resp = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| ScannerError::Http(e.to_string()))?;

        if !resp.status().is_success() {
            return Err(ScannerError::Http(format!("status {}", resp.status())));
        }

        // API may return a raw array
        let value: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| ScannerError::Parse(e.to_string()))?;

        let pairs: Vec<RawPair> = if value.is_array() {
            serde_json::from_value(value).map_err(|e| ScannerError::Parse(e.to_string()))?
        } else {
            let wrapped: TokenPairsResponse =
                serde_json::from_value(value).map_err(|e| ScannerError::Parse(e.to_string()))?;
            wrapped.pairs.unwrap_or_default()
        };

        Ok(pairs.into_iter().map(map_pair).collect())
    }
}

impl Default for DexScreenerClient {
    fn default() -> Self {
        Self::new()
    }
}

fn map_pair(p: RawPair) -> PairSummary {
    let (buys, sells) = extract_txns_h24(p.txns.as_ref());
    PairSummary {
        pair_address: p.pair_address,
        dex_id: p.dex_id,
        base_address: p.base_token.as_ref().and_then(|t| t.address.clone()),
        base_symbol: p.base_token.as_ref().and_then(|t| t.symbol.clone()),
        base_name: p.base_token.as_ref().and_then(|t| t.name.clone()),
        quote_address: p.quote_token.as_ref().and_then(|t| t.address.clone()),
        liquidity_usd: p.liquidity.and_then(|l| l.usd),
        txns_h24_buys: buys,
        txns_h24_sells: sells,
    }
}

fn extract_txns_h24(txns: Option<&serde_json::Value>) -> (Option<u64>, Option<u64>) {
    let Some(v) = txns else {
        return (None, None);
    };
    let Some(h24) = v.get("h24") else {
        return (None, None);
    };
    let buys = h24.get("buys").and_then(|x| x.as_u64());
    let sells = h24.get("sells").and_then(|x| x.as_u64());
    (buys, sells)
}
