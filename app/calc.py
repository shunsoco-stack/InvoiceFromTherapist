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


def calc_treatment_yen(
    unit_price_yen: int,
    quantity: int,
    rule: CommissionRule,
    coupon_discount_yen: int,
    hpb_discount_yen: int,
    p_points_yen: int,
    r_nomination_fee_yen: int,
) -> Tuple[int, int, int, int, int]:
    """
    Returns:
      (gross_menu_yen, discount_total_yen, net_menu_yen, sales_total_yen, staff_total_yen)

    Meanings:
    - coupon_discount_yen: menu-level coupon discount per treatment item
    - HPB: coupon discount amount (reduces customer payment)
    - P: points used amount (reduces customer payment)
    - R: nomination fee (adds to sales AND 100% paid to staff)

    Commission base:
    - percent: calculated on net_menu_yen (after HPB/P discounts)
    - fixed: fixed yen per item, not affected by discounts
    """
    q = max(1, int(quantity))
    unit_price = max(0, int(unit_price_yen))
    gross_menu = unit_price * q

    coupon = max(0, int(coupon_discount_yen)) * q
    hpb = max(0, int(hpb_discount_yen))
    pts = max(0, int(p_points_yen))
    discount_total = coupon + hpb + pts
    net_menu = max(0, gross_menu - discount_total)

    if rule.commission_type == "fixed":
        staff_menu = max(0, int(rule.commission_value)) * q
    else:
        pct = max(0, min(100, int(rule.commission_value)))
        staff_menu = (net_menu * pct) // 100

    r_fee = max(0, int(r_nomination_fee_yen))
    sales_total = net_menu + r_fee
    staff_total = staff_menu + r_fee
    return gross_menu, discount_total, net_menu, sales_total, staff_total

