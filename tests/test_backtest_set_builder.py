"""Unit tests for mt5api.backtest.set_builder."""
from __future__ import annotations

import pytest

from mt5api.backtest import set_builder


def test_build_set_renders_fixed_and_optimization_entries():
    text = set_builder.build_set({
        "comments": [
            "saved on 2026.05.15 08:30:02",
            "this file contains input parameters for testing/optimizing EA",
        ],
        "parameters": [
            {"name": "_Properties_", "value": "------"},
            {"name": "Magic_Number", "value": 1615044595},
            {"name": "Entry_Amount", "value": 0.01},
            {
                "name": "Stop_Loss",
                "value": 0,
                "start": 0,
                "step": 1,
                "stop": 10,
                "optimize": False,
            },
            {
                "name": "Take_Profit",
                "value": 92,
                "start": 80,
                "step": 4,
                "stop": 92,
                "optimize": True,
            },
            {"name": "Show_inds", "value": False},
        ],
    })

    assert text.splitlines() == [
        "; saved on 2026.05.15 08:30:02",
        "; this file contains input parameters for testing/optimizing EA",
        "_Properties_=------",
        "Magic_Number=1615044595",
        "Entry_Amount=0.01",
        "Stop_Loss=0||0||1||10||N",
        "Take_Profit=92||80||4||92||Y",
        "Show_inds=false",
    ]


def test_build_set_accepts_literal_y_n_optimize_values():
    text = set_builder.build_set({
        "parameters": [
            {
                "name": "Take_Profit",
                "value": 92,
                "start": 80,
                "step": 4,
                "stop": 92,
                "optimize": "Y",
            }
        ]
    })
    assert text == "Take_Profit=92||80||4||92||Y\n"


def test_build_set_rejects_missing_parameters():
    with pytest.raises(ValueError, match="parameters"):
        set_builder.build_set({})


def test_build_set_rejects_partial_range_fields():
    with pytest.raises(ValueError, match="missing optimization fields"):
        set_builder.build_set({
            "parameters": [{"name": "Take_Profit", "value": 92, "start": 80}]
        })


def test_build_set_rejects_bad_optimize_flag():
    with pytest.raises(ValueError, match="optimize"):
        set_builder.build_set({
            "parameters": [
                {
                    "name": "Take_Profit",
                    "value": 92,
                    "start": 80,
                    "step": 4,
                    "stop": 92,
                    "optimize": 1,
                }
            ]
        })