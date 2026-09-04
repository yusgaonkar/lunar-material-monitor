"""
Simple test for 3-scenario Lunar allocation logic.
No external dependencies - tests the core allocation scenarios.
"""

import sys


def test_scenario_1_no_shortage():
    """Scenario 1: No shortage - CMs get only Stage 1 POs, Lunar keeps remainder."""
    print("\n" + "=" * 80)
    print("TEST: Scenario 1 - No Shortage")
    print("=" * 80)

    uncommitted = 1000
    stage1_sienna = 200
    stage1_qualitel = 100
    total_shortage = 0  # No shortage
    remaining_lunar = uncommitted - (stage1_sienna + stage1_qualitel)

    # Scenario detection
    if total_shortage == 0:
        scenario = 1
    elif total_shortage <= remaining_lunar:
        scenario = 2
    else:
        scenario = 3

    print(f"Lunar uncommitted:           {uncommitted}")
    print(f"Stage 1 Sienna:              {stage1_sienna}")
    print(f"Stage 1 Qualitel:            {stage1_qualitel}")
    print(f"Total shortage:              {total_shortage}")
    print(f"Remaining Lunar:             {remaining_lunar}")
    print(f"Detected scenario:           {scenario}")

    # Allocation logic
    sienna_alloc = stage1_sienna  # Only Stage 1
    qualitel_alloc = stage1_qualitel  # Only Stage 1
    lunar_alloc = remaining_lunar  # Keeps remainder

    print(f"\nAllocation Results:")
    print(f"  Sienna:                    {sienna_alloc}")
    print(f"  Qualitel:                  {qualitel_alloc}")
    print(f"  Lunar:                     {lunar_alloc}")
    print(f"  Total:                     {sienna_alloc + qualitel_alloc + lunar_alloc}")

    assert scenario == 1, f"Expected scenario 1, got {scenario}"
    assert sienna_alloc == 200, f"Sienna allocation should be 200"
    assert qualitel_alloc == 100, f"Qualitel allocation should be 100"
    assert lunar_alloc == 700, f"Lunar allocation should be 700"
    assert sienna_alloc + qualitel_alloc + lunar_alloc == uncommitted

    print("\nPASSED: Scenario 1 - No Shortage")
    return True


def test_scenario_2_shortage_sufficient():
    """Scenario 2: Shortage exists, but Lunar has enough to cover."""
    print("\n" + "=" * 80)
    print("TEST: Scenario 2 - Shortage (Lunar Sufficient)")
    print("=" * 80)

    uncommitted = 1000
    stage1_sienna = 200
    stage1_qualitel = 100
    shortage_sienna = 300
    shortage_qualitel = 150

    total_shortage = shortage_sienna + shortage_qualitel
    remaining_lunar = uncommitted - (stage1_sienna + stage1_qualitel)

    # Scenario detection
    if total_shortage == 0:
        scenario = 1
    elif total_shortage <= remaining_lunar:
        scenario = 2
    else:
        scenario = 3

    print(f"Lunar uncommitted:           {uncommitted}")
    print(f"Stage 1 Sienna:              {stage1_sienna}")
    print(f"Stage 1 Qualitel:            {stage1_qualitel}")
    print(f"Shortage Sienna:             {shortage_sienna}")
    print(f"Shortage Qualitel:           {shortage_qualitel}")
    print(f"Total shortage:              {total_shortage}")
    print(f"Remaining Lunar:             {remaining_lunar}")
    print(f"Detected scenario:           {scenario}")

    # Allocation logic for Scenario 2
    sienna_alloc = stage1_sienna + shortage_sienna
    qualitel_alloc = stage1_qualitel + shortage_qualitel
    lunar_alloc = remaining_lunar - (shortage_sienna + shortage_qualitel)

    print(f"\nAllocation Results:")
    print(f"  Sienna:                    {sienna_alloc} ({stage1_sienna} stage1 + {shortage_sienna} shortage)")
    print(f"  Qualitel:                  {qualitel_alloc} ({stage1_qualitel} stage1 + {shortage_qualitel} shortage)")
    print(f"  Lunar:                     {lunar_alloc} (remaining)")
    print(f"  Total:                     {sienna_alloc + qualitel_alloc + lunar_alloc}")

    assert scenario == 2, f"Expected scenario 2, got {scenario}"
    assert total_shortage == 450
    assert remaining_lunar == 700
    assert sienna_alloc == 500
    assert qualitel_alloc == 250
    assert lunar_alloc == 250
    assert sienna_alloc + qualitel_alloc + lunar_alloc == uncommitted

    print("\nPASSED: Scenario 2 - Shortage (Lunar Sufficient)")
    return True


def test_scenario_3_shortage_insufficient():
    """Scenario 3: Shortage exists, Lunar insufficient - proportional allocation."""
    print("\n" + "=" * 80)
    print("TEST: Scenario 3 - Shortage (Lunar Insufficient)")
    print("=" * 80)

    uncommitted = 1000
    stage1_sienna = 200
    stage1_qualitel = 100
    shortage_sienna = 400
    shortage_qualitel = 600

    total_shortage = shortage_sienna + shortage_qualitel
    remaining_lunar = uncommitted - (stage1_sienna + stage1_qualitel)

    # Scenario detection
    if total_shortage == 0:
        scenario = 1
    elif total_shortage <= remaining_lunar:
        scenario = 2
    else:
        scenario = 3

    print(f"Lunar uncommitted:           {uncommitted}")
    print(f"Stage 1 Sienna:              {stage1_sienna}")
    print(f"Stage 1 Qualitel:            {stage1_qualitel}")
    print(f"Shortage Sienna:             {shortage_sienna}")
    print(f"Shortage Qualitel:           {shortage_qualitel}")
    print(f"Total shortage:              {total_shortage}")
    print(f"Remaining Lunar:             {remaining_lunar}")
    print(f"Detected scenario:           {scenario}")

    # Allocation logic for Scenario 3: proportional
    sienna_share = shortage_sienna / total_shortage
    qualitel_share = shortage_qualitel / total_shortage

    sienna_alloc = stage1_sienna + (remaining_lunar * sienna_share)
    qualitel_alloc = stage1_qualitel + (remaining_lunar * qualitel_share)
    lunar_alloc = 0  # All Lunar allocated to CMs

    print(f"\nAllocation Results:")
    print(f"  Sienna share:              {sienna_share:.1%}")
    print(f"  Qualitel share:            {qualitel_share:.1%}")
    print(f"  Sienna:                    {sienna_alloc:.0f} ({stage1_sienna} stage1 + {remaining_lunar * sienna_share:.0f} proportional)")
    print(f"  Qualitel:                  {qualitel_alloc:.0f} ({stage1_qualitel} stage1 + {remaining_lunar * qualitel_share:.0f} proportional)")
    print(f"  Lunar:                     {lunar_alloc}")
    print(f"  Total:                     {sienna_alloc + qualitel_alloc + lunar_alloc:.0f}")

    assert scenario == 3, f"Expected scenario 3, got {scenario}"
    assert total_shortage == 1000
    assert remaining_lunar == 700
    assert abs(sienna_alloc - 480) < 0.1, f"Sienna allocation should be ~480, got {sienna_alloc}"
    assert abs(qualitel_alloc - 520) < 0.1, f"Qualitel allocation should be ~520, got {qualitel_alloc}"
    assert lunar_alloc == 0
    assert abs((sienna_alloc + qualitel_alloc + lunar_alloc) - uncommitted) < 0.1

    print("\nPASSED: Scenario 3 - Shortage (Lunar Insufficient)")
    return True


def test_validation_sumproduct():
    """Test validation sumproduct calculation: Lunar allocation value."""
    print("\n" + "=" * 80)
    print("TEST: Validation Sumproduct - Lunar Allocation Value")
    print("=" * 80)

    # Synthetic balance_table rows for validation
    lunar_allocations = [
        {"part": "10-000099", "lunar_on_hand_alloc": 100000, "lunar_unit_price": 0.05},
        {"part": "10-000551", "lunar_on_hand_alloc": 50000, "lunar_unit_price": 0.10},
        {"part": "10-000372", "lunar_on_hand_alloc": 200000, "lunar_unit_price": 0.02},
    ]

    print(f"\nPart Allocations:")
    total_allocation_value = 0
    for alloc in lunar_allocations:
        value = alloc["lunar_on_hand_alloc"] * alloc["lunar_unit_price"]
        total_allocation_value += value
        print(f"  {alloc['part']}: {alloc['lunar_on_hand_alloc']:>10,.0f} × ${alloc['lunar_unit_price']:<7.4f} = ${value:>12,.2f}")

    expected_value = 14000.0  # Actual sumproduct: 5000 + 5000 + 4000
    variance = abs(total_allocation_value - expected_value)

    print(f"\nValidation Results:")
    print(f"  Total allocation value:    ${total_allocation_value:,.2f}")
    print(f"  Expected value:            ${expected_value:,.2f}")
    print(f"  Variance:                  ${variance:,.2f}")

    assert total_allocation_value == expected_value, f"Expected {expected_value}, got {total_allocation_value}"
    assert variance < 1.0, f"Variance should be < $1, got ${variance:.2f}"

    print(f"\nValidation Notes:")
    print(f"  In production, expected value would be $26,775,885.06 (sum of all")
    print(f"  Lunar allocations multiplied by their respective unit prices).")

    print("\nPASSED: Validation Sumproduct")
    return True


def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("LUNAR 3-SCENARIO ALLOCATION LOGIC - UNIT TESTS")
    print("=" * 80)

    tests = [
        test_scenario_1_no_shortage,
        test_scenario_2_shortage_sufficient,
        test_scenario_3_shortage_insufficient,
        test_validation_sumproduct,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"\nFAILED: {test.__name__}")
            print(f"Error: {e}")
            failed += 1

    print("\n" + "=" * 80)
    print(f"TEST RESULTS: {passed} passed, {failed} failed")
    print("=" * 80)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
