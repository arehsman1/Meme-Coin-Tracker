//! Meme Coin Tracker — Rust discovery scanner.
//!
//! Polls DexScreener for recent pairs on Solana / Ethereum / Base and
//! enqueues evaluate jobs on the Python API (`POST /api/v1/jobs/evaluate`).
//!
//! Alert-only: does not trade, sign, or hold keys.
//! Qualification remains authoritative in the Python engine.

use std::collections::HashSet;
use std::env;
use std::time::Duration;

use anyhow::{Context, Result};
use meme_chains::{discover_candidates, DexScreenerClient, RpcClient};
use meme_common::{Chain, EvaluateJobRequest, EvaluateJobResponse, };
use tracing::{info, warn};

struct Config {
    api_base: String,
    poll_interval: Duration,
    max_per_chain: usize,
    chains: Vec<Chain>,
    probe_rpc: bool,
}

impl Config {
    fn from_env() -> Self {
        let api_base = env::var("API_BASE_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8000".into())
            .trim_end_matches('/')
            .to_string();
        let poll_secs: u64 = env::var("SCANNER_POLL_SECONDS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(120);
        let max_per_chain: usize = env::var("SCANNER_MAX_PER_CHAIN")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(10);
        let chains = env::var("SCANNER_CHAINS")
            .ok()
            .map(|s| {
                s.split(',')
                    .filter_map(|p| Chain::parse(p.trim()))
                    .collect::<Vec<_>>()
            })
            .filter(|v| !v.is_empty())
            .unwrap_or_else(|| vec![Chain::Solana, Chain::Ethereum, Chain::Base]);
        let probe_rpc = env::var("SCANNER_PROBE_RPC")
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false);

        Self {
            api_base,
            poll_interval: Duration::from_secs(poll_secs),
            max_per_chain,
            chains,
            probe_rpc,
        }
    }
}

struct ApiClient {
    http: reqwest::Client,
    base: String,
}

impl ApiClient {
    fn new(base: String) -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(20))
                .build()
                .expect("http client"),
            base,
        }
    }

    async fn enqueue_evaluate(&self, chain: Chain, address: &str) -> Result<EvaluateJobResponse> {
        let url = format!("{}/api/v1/jobs/evaluate", self.base);
        let body = EvaluateJobRequest {
            chain: chain.as_str().to_string(),
            address: address.to_string(),
        };
        let resp = self
            .http
            .post(&url)
            .json(&body)
            .send()
            .await
            .with_context(|| format!("POST {url}"))?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            anyhow::bail!("enqueue failed status={status} body={text}");
        }
        let parsed: EvaluateJobResponse = resp.json().await.context("parse enqueue response")?;
        Ok(parsed)
    }

    async fn health(&self) -> Result<()> {
        let url = format!("{}/health", self.base);
        let resp = self.http.get(&url).send().await?;
        if resp.status().is_success() {
            Ok(())
        } else {
            anyhow::bail!("API health status {}", resp.status())
        }
    }
}

/// In-process dedupe so we don't spam the same address every poll.
struct Dedupe {
    seen: HashSet<String>,
    capacity: usize,
}

impl Dedupe {
    fn new(capacity: usize) -> Self {
        Self {
            seen: HashSet::new(),
            capacity,
        }
    }

    fn key(chain: Chain, address: &str) -> String {
        format!("{}:{}", chain.as_str(), address.to_ascii_lowercase())
    }

    fn insert_if_new(&mut self, chain: Chain, address: &str) -> bool {
        let k = Self::key(chain, address);
        if self.seen.contains(&k) {
            return false;
        }
        if self.seen.len() >= self.capacity {
            // crude eviction: clear half
            self.seen.clear();
        }
        self.seen.insert(k);
        true
    }
}

async fn run_cycle(
    cfg: &Config,
    dex: &DexScreenerClient,
    rpc: &RpcClient,
    api: &ApiClient,
    dedupe: &mut Dedupe,
) -> Result<(usize, usize)> {
    let mut discovered = 0usize;
    let mut enqueued = 0usize;

    for chain in &cfg.chains {
        match discover_candidates(dex, *chain, cfg.max_per_chain).await {
            Ok(candidates) => {
                discovered += candidates.len();
                for c in candidates {
                    if !dedupe.insert_if_new(c.chain, &c.address) {
                        continue;
                    }
                    if cfg.probe_rpc {
                        match rpc.probe(c.chain, &c.address).await {
                            Ok(probe) if probe.exists == Some(false) => {
                                warn!(
                                    chain = %c.chain,
                                    address = %c.address,
                                    "rpc probe says missing — still enqueue for engine honesty"
                                );
                            }
                            Ok(_) => {}
                            Err(e) => warn!(error = %e, "rpc probe failed"),
                        }
                    }
                    match api.enqueue_evaluate(c.chain, &c.address).await {
                        Ok(resp) => {
                            enqueued += 1;
                            info!(
                                chain = %c.chain,
                                address = %c.address,
                                symbol = ?c.symbol,
                                job_id = %resp.job_id,
                                "enqueued evaluate"
                            );
                        }
                        Err(e) => {
                            warn!(
                                chain = %c.chain,
                                address = %c.address,
                                error = %e,
                                "enqueue failed"
                            );
                        }
                    }
                }
            }
            Err(e) => {
                warn!(chain = %chain, error = %e, "discovery failed");
            }
        }
    }

    // silence unused import warning path for TokenCandidate in some builds
    let _: Option<TokenCandidate> = None;
    Ok((discovered, enqueued))
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cfg = Config::from_env();
    info!(
        api = %cfg.api_base,
        poll_secs = cfg.poll_interval.as_secs(),
        max_per_chain = cfg.max_per_chain,
        chains = ?cfg.chains.iter().map(|c| c.as_str()).collect::<Vec<_>>(),
        probe_rpc = cfg.probe_rpc,
        "meme-scanner starting (alert-only, no trading)"
    );

    let api = ApiClient::new(cfg.api_base.clone());
    match api.health().await {
        Ok(()) => info!("API health OK"),
        Err(e) => warn!(error = %e, "API not reachable yet — will retry each cycle"),
    }

    let dex = DexScreenerClient::new();
    let rpc = RpcClient::from_env();
    let mut dedupe = Dedupe::new(5_000);

    loop {
        match run_cycle(&cfg, &dex, &rpc, &api, &mut dedupe).await {
            Ok((discovered, enqueued)) => {
                info!(discovered, enqueued, "cycle complete");
            }
            Err(e) => warn!(error = %e, "cycle error"),
        }

        tokio::select! {
            _ = tokio::time::sleep(cfg.poll_interval) => {}
            _ = tokio::signal::ctrl_c() => {
                info!("shutdown signal");
                break;
            }
        }
    }

    Ok(())
}
