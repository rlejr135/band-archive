import hashlib
import tomllib
from pathlib import Path


BACKEND = Path(__file__).parents[1]
EXPECTED_CA_SHA256 = '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7'


def test_fly_deploy_packages_the_pinned_postgres_ca_at_sslrootcert_path():
    config = tomllib.loads((BACKEND / 'fly.toml').read_text(encoding='utf-8'))
    ca_path = BACKEND / 'certs' / 'supabase-prod-ca-2021.crt'

    assert config['files'] == [{
        'guest_path': '/app/certs/supabase-prod-ca-2021.crt',
        'local_path': 'certs/supabase-prod-ca-2021.crt',
    }]
    assert hashlib.sha256(ca_path.read_bytes()).hexdigest() == EXPECTED_CA_SHA256
