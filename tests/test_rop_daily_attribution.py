from decimal import Decimal

from app.services.rop_daily import _partition_focus_attribution, _responsible_label
from app.services.rop_directory import EmployeeIdentity, RopDirectory
from app.services.rop_mvp3 import FocusDeal


def _deal(assigned_by_id: str, severity: str, bucket: str) -> FocusDeal:
    return FocusDeal(
        deal_id=f"deal-{assigned_by_id}-{severity}-{bucket}",
        category_id="7",
        stage_id="C7:EXECUTING",
        assigned_by_id=assigned_by_id,
        currency="RUB",
        opportunity=Decimal("100000"),
        age_days=10,
        attention_days=8,
        critical_days=24,
        severity=severity,
        rule_label="test",
        business_bucket=bucket,
    )


def test_daily_partitions_focus_to_directory_users_only() -> None:
    directory = RopDirectory(
        users={
            "10": EmployeeIdentity(
                user_id="10",
                display_name="Иван Петров",
                active=True,
                department_ids=(),
                department_names=(),
            )
        },
        departments={},
    )
    deals = (
        _deal("10", "critical", "money"),
        _deal("7912", "critical", "money"),
        _deal("484", "attention", "hygiene"),
    )
    human, excluded = _partition_focus_attribution(deals, directory)
    assert set(human) == {"10"}
    assert set(excluded) == {"7912", "484"}
    assert human["10"]["critical"] == 1
    assert excluded["7912"]["critical_money"] == 1
    assert excluded["484"]["attention"] == 1
    assert "Иван Петров" in _responsible_label(directory, "10")
    assert _responsible_label(directory, "7912") == (
        "actor ID 7912 · исключён из manager attribution"
    )
