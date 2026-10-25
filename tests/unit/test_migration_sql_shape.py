"""Static checks on SQL migrations (no live database required)."""

from pathlib import Path

MIG_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_migrations_exist():
    files = sorted(MIG_DIR.glob("*.sql"))
    assert len(files) >= 2
    names = [f.name for f in files]
    assert any("000001" in n or "initial" in n for n in names)
    assert any("000002" in n or "roles" in n for n in names)


def test_no_destructive_cascade():
    for f in MIG_DIR.glob("*.sql"):
        text = f.read_text().upper()
        assert "DROP SCHEMA" not in text
        assert "CASCADE" not in text or "ON DELETE CASCADE" in f.read_text().upper()
        # Allow ON DELETE CASCADE on FKs only — ban DROP ... CASCADE
        for line in f.read_text().splitlines():
            lu = line.upper().strip()
            if lu.startswith("DROP") and "CASCADE" in lu:
                assert False, f"Destructive DROP CASCADE in {f.name}: {line}"


def test_roles_migration_contains_required_objects():
    path = next(MIG_DIR.glob("*roles*"))
    text = path.read_text()
    for needle in [
        "admin_allowlist",
        "role_permissions",
        "has_app_role",
        "is_telegram_admin",
        "owner",
        "service_worker",
        "ENABLE ROW LEVEL SECURITY",
        "scanner_service_settings",
    ]:
        assert needle in text, f"missing {needle}"


def test_initial_migration_has_core_tables():
    path = next(MIG_DIR.glob("*initial*"))
    text = path.read_text()
    for table in [
        "tokens",
        "qualification_evaluations",
        "notification_outbox",
        "scanner_job_runs",
        "historical_outcomes",
        "audit_logs",
    ]:
        assert table in text
