"""Budget unit tests — exercise the typed budget contract."""

import pytest

from ai_sre.exceptions import BudgetExhausted


@pytest.mark.unit
def test_budget_records_tool_call() -> None:
    from ai_sre.core.investigation.budget import Budget

    b = Budget(max_tool_calls=2)
    b.assert_can_call_tool()
    b.record_tool_call()
    b.assert_can_call_tool()
    b.record_tool_call()

    with pytest.raises(BudgetExhausted):
        b.assert_can_call_tool()


@pytest.mark.unit
def test_investigation_cost_cap_blocks() -> None:
    from ai_sre.core.investigation.budget import Budget

    b = Budget(max_llm_cost_usd=0.10)
    b.record_llm_call(tokens=100, cost_usd=0.10)
    with pytest.raises(BudgetExhausted, match="LLM cost budget"):
        b.assert_can_call_llm()


@pytest.mark.unit
def test_tenant_cost_cap_blocks_when_prior_spend_fills_it() -> None:
    """Per-tenant rolling cap (spec 0017): spend accrued by *other*
    investigations counts against this one's allowance."""
    from ai_sre.core.investigation.budget import Budget

    b = Budget(max_llm_cost_usd=0.50, max_tenant_cost_usd=1.0, tenant_cost_used_usd=0.9)
    b.assert_can_call_llm()  # 0.9 < 1.0 — still allowed
    b.record_llm_call(tokens=100, cost_usd=0.10)
    with pytest.raises(BudgetExhausted, match="Tenant LLM cost budget"):
        b.assert_can_call_llm()


@pytest.mark.unit
def test_tenant_cost_cap_zero_disables() -> None:
    from ai_sre.core.investigation.budget import Budget

    b = Budget(max_tenant_cost_usd=0.0, tenant_cost_used_usd=10_000.0)
    b.assert_can_call_llm()  # no tenant cap configured


@pytest.mark.unit
def test_snapshot_includes_tenant_cap_fields() -> None:
    from ai_sre.core.investigation.budget import Budget

    snap = Budget(max_tenant_cost_usd=25.0, tenant_cost_used_usd=1.5).snapshot()
    assert snap["max_tenant_cost_usd"] == 25.0
    assert snap["tenant_cost_used_usd"] == 1.5
