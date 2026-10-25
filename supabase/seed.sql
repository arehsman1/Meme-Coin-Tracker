-- Optional seed data for local development only.
-- Do not run against production.

INSERT INTO public.provider_registry (name, chain, capabilities, auth_required, rate_limit_per_minute)
VALUES
    ('helius', 'solana', ARRAY['rpc', 'das', 'webhooks'], TRUE, 50),
    ('alchemy', 'ethereum', ARRAY['rpc', 'nft', 'transfers'], TRUE, 100),
    ('alchemy', 'base', ARRAY['rpc', 'transfers'], TRUE, 100),
    ('dexscreener', NULL, ARRAY['pairs', 'tokens', 'search'], FALSE, 60)
ON CONFLICT DO NOTHING;
