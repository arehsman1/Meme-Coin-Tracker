//! Minimal JSON-RPC helpers for Solana and EVM.
//! Used for lightweight existence probes — not full qualification.

use meme_common::{Chain, Result, ScannerError};
use reqwest::Client;
use serde_json::{json, Value};
use tracing::debug;

#[derive(Debug, Clone)]
pub struct RpcClient {
    http: Client,
    solana_url: Option<String>,
    ethereum_url: Option<String>,
    base_url: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RpcProbe {
    pub chain: Chain,
    pub address: String,
    pub exists: Option<bool>,
    pub detail: String,
}

impl RpcClient {
    pub fn from_env() -> Self {
        Self {
            http: Client::builder()
                .timeout(std::time::Duration::from_secs(12))
                .build()
                .expect("reqwest"),
            solana_url: std::env::var("SOLANA_RPC_URL").ok().filter(|s| !s.is_empty()),
            ethereum_url: std::env::var("ETHEREUM_RPC_URL")
                .ok()
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    std::env::var("ALCHEMY_API_KEY").ok().map(|k| {
                        format!("https://eth-mainnet.g.alchemy.com/v2/{k}")
                    })
                }),
            base_url: std::env::var("BASE_RPC_URL")
                .ok()
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    std::env::var("ALCHEMY_API_KEY").ok().map(|k| {
                        format!("https://base-mainnet.g.alchemy.com/v2/{k}")
                    })
                }),
        }
    }

    fn url_for(&self, chain: Chain) -> Option<&str> {
        match chain {
            Chain::Solana => self.solana_url.as_deref(),
            Chain::Ethereum => self.ethereum_url.as_deref(),
            Chain::Base => self.base_url.as_deref(),
        }
    }

    pub async fn probe(&self, chain: Chain, address: &str) -> Result<RpcProbe> {
        let Some(url) = self.url_for(chain) else {
            return Ok(RpcProbe {
                chain,
                address: address.to_string(),
                exists: None,
                detail: "RPC URL not configured".into(),
            });
        };

        match chain {
            Chain::Solana => self.probe_solana(url, address).await,
            Chain::Ethereum | Chain::Base => self.probe_evm(chain, url, address).await,
        }
    }

    async fn probe_solana(&self, url: &str, address: &str) -> Result<RpcProbe> {
        let body = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "base64"}]
        });
        let value = self.post_rpc(url, body).await?;
        let exists = value
            .pointer("/result/value")
            .map(|v| !v.is_null());
        Ok(RpcProbe {
            chain: Chain::Solana,
            address: address.to_string(),
            exists,
            detail: if exists == Some(true) {
                "account present".into()
            } else if exists == Some(false) {
                "account missing".into()
            } else {
                "unexpected response".into()
            },
        })
    }

    async fn probe_evm(&self, chain: Chain, url: &str, address: &str) -> Result<RpcProbe> {
        let body = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getCode",
            "params": [address, "latest"]
        });
        let value = self.post_rpc(url, body).await?;
        let code = value
            .pointer("/result")
            .and_then(|v| v.as_str())
            .unwrap_or("0x");
        let exists = Some(code != "0x" && code != "0x0");
        Ok(RpcProbe {
            chain,
            address: address.to_string(),
            exists,
            detail: if exists == Some(true) {
                "contract code present".into()
            } else {
                "no contract code".into()
            },
        })
    }

    async fn post_rpc(&self, url: &str, body: Value) -> Result<Value> {
        debug!(%url, "json-rpc call");
        let resp = self
            .http
            .post(url)
            .json(&body)
            .send()
            .await
            .map_err(|e| ScannerError::Http(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(ScannerError::Http(format!("rpc http {}", resp.status())));
        }
        resp.json()
            .await
            .map_err(|e| ScannerError::Parse(e.to_string()))
    }
}
