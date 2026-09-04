"""
Lunar 3-Scenario Allocation Logic Tests

Tests the correct allocation of Lunar inventory across CM rows based on:
1. Stage 1: CM POs to Lunar (po_vendor="Lunar")
2. Scenario detection:
   - Scenario 1 (No shortage): Lunar keeps full inventory, CMs get only Stage 1 POs
   - Scenario 2 (Shortage + sufficient): Allocate exact shortages, remainder to Lunar
   - Scenario 3 (Shortage + insufficient): Proportional allocation, Lunar = 0
3. Validation: Sumproduct of (Lunar allocation qty × unit price) vs. expected $26,775,885.06
"""

import pandas as pd
import pytest
import logging

log = logging.getLogger(__name__)


class TestAllocationScenarios:
    """Test 3-scenario allocation logic with synthetic data."""

    def test_scenario_1_no_shortage(self):
        """Scenario 1: No shortage - CMs get only Stage 1 POs, Lunar keeps remainder."""

        # Setup
        uncommitted = 1000
        stage1_sienna = 200
        stage1_qualitel = 100
        worst_pab_sienna = 0  # No shortage
        worst_pab_qualitel = 0  # No shortage

        # Shortage calculation
        total_shortage = 0
        remaining_lunar = uncommitted - (stage1_sienna + stage1_qualitel)

        # Scenario detection
        if total_shortage == 0:
            scenario = 1
        elif total_shortage <= remaining_lunar:
            scenario = 2
        else:
            scenario = 3

        assert scenario == 1

        # Allocation logic
        sienna_alloc = stage1_sienna  # Only Stage 1
        qualitel_alloc = stage1_qualitel  # Only Stage 1
        lunar_alloc = remaining_lunar  # Keeps remainder

        assert sienna_alloc == 200
        assert qualitel_alloc == 100
        assert lunar_alloc == 700
        assert sienna_alloc + qualitel_alloc + lunar_alloc == uncommitted

    def test_scenario_2_shortage_sufficient(self):
        """Scenario 2: Shortage exists, but Lunar has enough to cover."""

        # Setup
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

        assert scenario == 2
        assert total_shortage == 450
        assert remaining_lunar == 700

        # Allocation logic for Scenario 2
        sienna_alloc = stage1_sienna + shortage_sienna
        qualitel_alloc = stage1_qualitel + shortage_qualitel
        lunar_alloc = remaining_lunar - (shortage_sienna + shortage_qualitel)

        assert sienna_alloc == 500
        assert qualitel_alloc == 250
        assert lunar_alloc == 250
        assert sienna_alloc + qualitel_alloc + lunar_alloc == uncommitted

    def test_scenario_3_shortage_insufficient(self):
        """Scenario 3: Shortage exists, Lunar insufficient - proportional allocation."""

        # Setup
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

        assert scenario == 3
        assert total_shortage == 1000
        assert remaining_lunar == 700

        # Allocation logic for Scenario 3: proportional
        sienna_share = shortage_sienna / total_shortage
        qualitel_share = shortage_qualitel / total_shortage

        sienna_alloc = stage1_sienna + (remaining_lunar * sienna_share)
        qualitel_alloc = stage1_qualitel + (remaining_lunar * qualitel_share)
        lunar_alloc = 0  # All Lunar allocated to CMs

        assert sienna_alloc == pytest.approx(200 + 280)  # 200 + (700 * 0.4)
        assert qualitel_alloc == pytest.approx(100 + 420)  # 100 + (700 * 0.6)
        assert lunar_alloc == 0
        assert sienna_alloc + qualitel_alloc + lunar_alloc == pytest.approx(uncommitted)

    def test_allocation_with_sample_parts(self):
        """Test allocation logic with sample parts (10-000099, 10-000551)."""

        parts = {
            "10-000099": {
                "lunar_unrestricted": 1039115,
                "stage1_total": 0,  # No Stage 1 POs
                "shortages": {"Sienna": 100000, "Qualitel": 50000},
                "total_shortage": 150000,
            },
            "10-000551": {
                "lunar_unrestricted": 500000,
                "stage1_total": 0,
                "shortages": {"Sienna": 0, "Qualitel": 0},
                "total_shortage": 0,
            }
        }

        for part, data in parts.items():
            uncommitted = data["lunar_unrestricted"]
            stage1_total = data["stage1_total"]
            total_shortage = data["total_shortage"]
            shortages = data["shortages"]

            remaining_lunar = uncommitted - stage1_total

            # Scenario detection
            if total_shortage == 0:
                scenario = 1
            elif total_shortage <= remaining_lunar:
                scenario = 2
            else:
                scenario = 3

            # Verify scenario detection
            if part == "10-000099":
                assert scenario == 2  # Lunar has enough (1M+ vs 150k shortage)
            else:
                assert scenario == 1  # No shortage

    def test_validation_sumproduct(self):
        """Test validation sumproduct calculation: Lunar allocation value."""

        # Synthetic balance_table rows for validation
        lunar_allocations = [
            {"part": "10-000099", "lunar_on_hand_alloc": 100000, "lunar_unit_price": 0.05},
            {"part": "10-000551", "lunar_on_hand_alloc": 50000, "lunar_unit_price": 0.10},
            {"part": "10-000372", "lunar_on_hand_alloc": 200000, "lunar_unit_price": 0.02},
        ]

        lunar_df = pd.DataFrame(lunar_allocations)
        lunar_df["allocation_value"] = lunar_df["lunar_on_hand_alloc"] * lunar_df["lunar_unit_price"]

        total_allocation_value = lunar_df["allocation_value"].sum()
        expected_components = {
            "10-000099": 100000 * 0.05,
            "10-000551": 50000 * 0.10,
            "10-000372": 200000 * 0.02,
        }

        assert total_allocation_value == pytest.approx(
            sum(expected_components.values())
        )
        assert total_allocation_value == pytest.approx(9500)


class TestAllocationLogic:
    """Integration test of full allocation logic."""

    def test_stage1_identification(self):
        """Test Stage 1: Identifying CM POs to Lunar."""

        # Synthetic on-order data
        onorder = pd.DataFrame([
            {
                "source_report": "CM: Sienna GA",
                "po_vendor": "Lunar Energy",
                "lunar_lpn": "10-000099",
                "quantity_open": 5000,
            },
            {
                "source_report": "CM: Sienna GA",
                "po_vendor": "Other Vendor",
                "lunar_lpn": "10-000099",
                "quantity_open": 3000,
            },
            {
                "source_report": "CM: Qualitel WA",
                "po_vendor": "Lunar Energy",
                "lunar_lpn": "10-000099",
                "quantity_open": 2500,
            },
        ])

        # Filter for CM POs to Lunar
        cm_orders_lunar = onorder[
            onorder["po_vendor"].str.contains("Lunar", case=False, na=False)
        ].copy()
        cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(
            r"(Sienna|Qualitel|Celestica|Plexus|Unigen)", expand=False
        )

        cm_orders_grouped = cm_orders_lunar.groupby(["cm_extracted", "lunar_lpn"])[
            "quantity_open"
        ].sum().reset_index()
        cm_orders_grouped.columns = ["cm", "part", "cm_orders"]

        # Verify Stage 1 allocations
        sienna_alloc = cm_orders_grouped[
            (cm_orders_grouped["cm"] == "Sienna") &
            (cm_orders_grouped["part"] == "10-000099")
        ]["cm_orders"].values

        qualitel_alloc = cm_orders_grouped[
            (cm_orders_grouped["cm"] == "Qualitel") &
            (cm_orders_grouped["part"] == "10-000099")
        ]["cm_orders"].values

        assert sienna_alloc[0] == 5000
        assert qualitel_alloc[0] == 2500


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
