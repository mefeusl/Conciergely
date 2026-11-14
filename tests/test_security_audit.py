"""Guards for the launch security audit (docs/security-audit.md): they fail if a later change undoes a protection."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("app", "site", "deploy", "relay", "docs", "tests", "tools")
SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9]{32,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"EAA[A-Za-z0-9]{60,}",  # Meta access tokens
    r"xox[baprs]-[A-Za-z0-9-]{10,}", r"ghp_[A-Za-z0-9]{30,}",
]


def _files(*suffixes, dirs=SOURCE_DIRS):
    for d in dirs:
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts:
                yield p


def test_no_secrets_written_into_any_project_file():
    for p in list(_files(".py", ".html", ".js", ".sh", ".md", ".txt", ".svg", ".service")) + [ROOT / ".env.example"]:
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            assert not re.search(pat, text), "%s looks like it contains a secret (%s)" % (p.relative_to(ROOT), pat)


def test_real_env_file_is_never_part_of_the_project():
    assert not (ROOT / ".env").exists() and (ROOT / ".env.example").is_file()
    assert ".env" in (ROOT / ".gitignore").read_text().split()


def test_env_example_has_no_filled_in_secret_values():
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and re.search(r"(_KEY|_TOKEN|SECRET|PASSWORD|_SID)$", key) and not key.startswith("#"):
            assert value.strip() in ("", '""'), "%s must be empty in the example file" % key


def test_browser_visible_files_hold_no_config_names_or_keys():
    for p in (ROOT / "site").rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            for word in ("ANTHROPIC", "OPENAI", "META_ACCESS", "ADMIN_PASSWORD", "MASTER_KEY", "RELAY_KEY", "api_key", "apikey"):
                assert word not in text, "%s mentions %s" % (p.relative_to(ROOT), word)


def test_every_admin_route_goes_through_the_login_guard():
    src = (ROOT / "app" / "admin.py").read_text()
    routes = re.findall(r"@router\.(?:get|post)\([^\n]*\)", src)
    assert len(routes) >= 10
    for r in routes:
        assert "dependencies=[Depends(guard)]" in r, "admin route without login: " + r


def test_every_admin_post_checks_the_form_token():
    src = (ROOT / "app" / "admin.py").read_text()
    for block in re.split(r"\n(?=@router\.post)", src)[1:]:
        body = block.split("\n@router.", 1)[0]
        assert "_csrf(csrf)" in body, "admin POST without CSRF check: " + body.splitlines()[0]


def test_only_known_places_build_sql_from_text():
    """Every other query is parameterised. A new f-string query must be added here on purpose, after a review."""
    hits = []
    for p in _files(".py", dirs=("app",)):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r"execute\(\s*f[\"']", line) or "executescript" in line:
                hits.append("%s:%s" % (p.name, line.strip()[:70]))
    assert len(hits) == 6, hits


def test_unsigned_twilio_messages_are_refused():
    main = (ROOT / "app" / "main.py").read_text()
    assert "if not config.PUBLIC_BASE_URL or not wa_twilio.validate(" in main


def test_webhook_size_is_capped_and_signature_is_required_for_meta():
    main = (ROOT / "app" / "main.py").read_text()
    assert "MAX_BODY" in main and "wa_meta.verify_signature" in main and "compare_digest" in main


def test_service_is_sandboxed():
    unit = (ROOT / "deploy" / "wa-assistant.service").read_text()
    for line in ("User=wabot", "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateDevices=true",
                 "CapabilityBoundingSet=", "UMask=0077", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
                 "--host 127.0.0.1"):
        assert line in unit, line


def test_install_script_locks_down_files_and_keeps_dev_files_off_the_server():
    sh = (ROOT / "deploy" / "install.sh").read_text()
    assert 'chmod 700 "$APP_DIR/data"' in sh and 'chmod 600 "$APP_DIR/.env"' in sh
    for excluded in ("--exclude tests", "--exclude relay", "--exclude tools", "--exclude docs", "--exclude .env"):
        assert excluded in sh
    assert "ufw allow OpenSSH" in sh and "fail2ban" in sh and "encode zstd gzip" in sh


def test_no_unsafe_python_calls():
    for p in _files(".py", dirs=("app", "relay")):
        text = p.read_text()
        for bad in ("verify=False", "shell=True", "pickle.loads", "yaml.load(", "os.system("):
            assert bad not in text, "%s uses %s" % (p.name, bad)
        assert not re.search(r"(?<![.\w])eval\(|(?<![.\w])exec\(", text), p.name


def test_logs_never_carry_a_full_phone_number_or_message_text():
    for p in _files(".py", dirs=("app",)):
        for line in p.read_text().splitlines():
            if re.search(r"\blog\w*\.(info|warning|error|exception|debug)\(", line):
                tail = line.split('",', 1)[-1]
                assert not re.search(r"\b(phone|body|password|secret)\b\s*[,)]", tail) or "last4" in line, line.strip()


def test_ops_log_hides_full_numbers(caplog):
    import logging
    from app import ops
    with caplog.at_level(logging.WARNING):
        ops.event("upgrade", "Ali (+905551234567) asked for Plus", notify=False)
    assert "+905551234567" not in caplog.text and "...4567" in caplog.text
