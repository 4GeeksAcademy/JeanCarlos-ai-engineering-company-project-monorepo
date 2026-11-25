"""OWASP A02: PostgreSQL connections to remote hosts are always encrypted (sslmode=require)."""

from sqlalchemy.engine import make_url

from data.pipelines.connections import require_tls


def test_a_remote_host_without_sslmode_gets_require():
    url = require_tls(make_url("postgresql+psycopg://writer:pw@aws-1-eu-west-2.pooler.supabase.com:6543/postgres"))
    assert url.query["sslmode"] == "require"


def test_local_and_docker_hosts_and_explicit_modes_are_kept():
    for raw in ("postgresql+psycopg://u:p@127.0.0.1:55432/healthcore", "postgresql+psycopg://u:p@localhost/x",
                "postgresql+psycopg://u:p@db:5432/healthcore"):
        assert "sslmode" not in require_tls(make_url(raw)).query
    explicit = make_url("postgresql+psycopg://u:p@db.example.org/x?sslmode=verify-full")
    assert require_tls(explicit).query["sslmode"] == "verify-full"
