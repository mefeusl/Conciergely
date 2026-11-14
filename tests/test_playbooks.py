import json
from types import SimpleNamespace

from app import assistant, playbooks, tools
from tests.conftest import PHONE, FakeAnthropic, response, text_block, tool_block

EXPECTED = {"chart-scanner", "news-radar", "timing-planner", "risk-manager", "time-machine", "setup-backtester",
            "pre-mortem", "trade-journal-coach", "portfolio-review", "market-pulse-briefing", "filing-reader", "watchlist-scan"}


def test_all_twelve_load_with_guardrails():
    assert set(playbooks.names()) == EXPECTED
    for n in EXPECTED:
        body = playbooks.get(n)
        assert body and "Guardrails" in body or n == "pre-mortem"
        assert not body.startswith("---")


def test_tool_returns_body_and_refuses_bad_names(client):
    r = json.loads(tools.run(PHONE, "use_playbook", {"name": "risk-manager"}))
    assert "Risk Manager" in r["instructions"] and "guardrails override" in r["note"]
    for bad in ("../../etc/passwd", "RISK-MANAGER", "", None, 5, "nope"):
        out = json.loads(tools.run(PHONE, "use_playbook", {"name": bad}))
        assert "error" in out and set(out["available"]) == EXPECTED
    assert playbooks.get("../app/db") is None


def test_index_in_system_prompt_is_short(client):
    system = assistant.build_system(client)
    for n in EXPECTED:
        assert f"  {n}:" in system
    assert "Guardrails" not in system  # full bodies are loaded on demand only
    assert len(playbooks.index()) < 2500


def test_playbook_flow_end_to_end(client):
    fake = FakeAnthropic(
        response(SimpleNamespace(type="tool_use", id="p1", name="use_playbook", input={"name": "pre-mortem"}), stop="tool_use"),
        response(text_block("• Fikir yanlış • Zamanlama • Dış şok")),
    )
    assistant.set_client(fake)
    assert "Zamanlama" in assistant.reply(client, "THYAO alacağım")
    assert "Pre-Mortem" in json.dumps(fake.calls[1]["messages"][-1]["content"], ensure_ascii=False)
