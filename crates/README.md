# Rust crates — Meme Coin Tracker

High-throughput **discovery / ingestion** path. Qualification remains in Python.

| Crate | Role |
|-------|------|
| `meme_common` | Chain enum, `TokenCandidate`, job request types |
| `meme_chains` | DexScreener client + optional Solana/EVM RPC probes |
| `meme_scanner` | Binary: poll → dedupe → `POST /api/v1/jobs/evaluate` |

**Does not trade. Does not sign. Does not hold keys.**

## Build

```bash
# from repo root
cargo build -p meme_scanner --release
```

## Run

```bash
export API_BASE_URL=http://127.0.0.1:8000
# optional
export SCANNER_POLL_SECONDS=120
export SCANNER_MAX_PER_CHAIN=10
export SCANNER_CHAINS=solana,ethereum,base
export SCANNER_PROBE_RPC=false
# optional RPC for existence probes
export SOLANA_RPC_URL=https://api.mainnet-beta.solana.com

cargo run -p meme_scanner
# or
./target/release/meme-scanner
```

Requires the Python API + worker running so evaluate jobs are processed.

## Relation to Python discovery

Python worker already has a DexScreener discovery loop. The Rust scanner is an
**additional / alternative** high-throughput path that enqueues the same job type.
Running both is safe thanks to job/token dedupe on the backend and in-process
dedupe in the scanner.
