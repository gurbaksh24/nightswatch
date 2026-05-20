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
