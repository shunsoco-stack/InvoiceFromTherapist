from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


CommissionType = str  # 'percent' | 'fixed'


@dataclass(frozen=True)
class CommissionRule:
    commission_type: CommissionType
    commission_value: int


def _normalize_rule(commission_type: Optional[str], commission_value: Optional[int]) -> Optional[CommissionRule]:
    if commission_type is None or commission_value is None:
        return None
    if commission_type not in ("percent", "fixed"):
        return None
    return CommissionRule(commission_type=commission_type, commission_value=int(commission_value))


def pick_commission_rule(
    override_type: Optional[str],
    override_value: Optional[int],
    menu_type: Optional[str],
    menu_value: Optional[int],
    therapist_type: str,
    therapist_value: int,
) -> CommissionRule:
    """
    Priority: treatment override > menu override > therapist default.
    """
    for t, v in (
        (override_type, override_value),
        (menu_type, menu_value),
        (therapist_type, therapist_value),
    ):
        rule = _normalize_rule(t, v)
        if rule is not None:
            return rule
    return CommissionRule(commission_type="percent", commission_value=50)


def calc_payout_yen(
    price_yen: int,
    quantity: int,
    rule: CommissionRule,
) -> Tuple[int, int]:
    """
    Returns (sales_yen, payout_yen).

    - percent: payout = round_down(sales * percent / 100)
    - fixed: payout = fixed_yen * quantity
    """
    q = max(1, int(quantity))
    price = max(0, int(price_yen))
    sales = price * q
    if rule.commission_type == "fixed":
        payout = max(0, int(rule.commission_value)) * q
        return sales, payout
    pct = max(0, min(100, int(rule.commission_value)))
    payout = (sales * pct) // 100
    return sales, payout

