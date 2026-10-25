//! Shared types for the Meme Coin Tracker scanner.
//! Alert-only: no trading, no wallet signing.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Supported chains (v0.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Chain {
    Solana,
    Ethereum,
    Base,
}

impl Chain {
    pub fn as_str(self) -> &'static str {
        match self {
            Chain::Solana => "solana",
            Chain::Ethereum => "ethereum",
            Chain::Base => "base",
        }
    }

    pub fn dexscreener_id(self) -> &'static str {
        self.as_str()
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "solana" | "sol" => Some(Chain::Solana),
            "ethereum" | "eth" => Some(Chain::Ethereum),
            "base" => Some(Chain::Base),
            _ => None,
        }
    }
}

impl std::fmt::Display for Chain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A newly observed token candidate from discovery/ingestion.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenCandidate {
    pub chain: Chain,
    pub address: String,
    pub symbol: Option<String>,
    pub name: Option<String>,
    pub pair_address: Option<String>,
    pub dex: Option<String>,
    pub liquidity_usd: Option<f64>,
    pub discovered_at: DateTime<Utc>,
    pub source: String,
}

/// Request body for Python API `POST /api/v1/jobs/evaluate`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvaluateJobRequest {
    pub chain: String,
    pub address: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvaluateJobResponse {
    pub job_id: String,
    pub status: String,
    #[serde(default)]
    pub storage: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum ScannerError {
    #[error("http error: {0}")]
    Http(String),
    #[error("parse error: {0}")]
    Parse(String),
    #[error("config error: {0}")]
    Config(String),
    #[error("unsupported chain: {0}")]
    UnsupportedChain(String),
}

pub type Result<T> = std::result::Result<T, ScannerError>;
