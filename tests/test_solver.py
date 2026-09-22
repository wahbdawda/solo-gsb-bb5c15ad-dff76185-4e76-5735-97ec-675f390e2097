"""求解器单元测试:可行解、单液上限导致不足、体积约束导致过量、水量约束。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.solver import solve  # noqa: E402


def case_feasible():
    req = {
        "stocks": [
            # A 液:硝酸钙母液,1 L 可提供 N 75mg/L@100L、Ca 160mg/L@100L、Fe 1mg/L@100L
            # v_A≈1.5L@100L: N=150,Ca=200,Fe=2
            {"name": "A钙氮液", "concentrations": {"N": 10000.0, "Ca": 13334.0, "Fe": 134.0},
             "available_volume": 5.0},
            # v_B≈1.4L@100L: P=50,K=210,Mg=40
            {"name": "B磷钾液", "concentrations": {"P": 3572.0, "K": 15000.0, "Mg": 2858.0},
             "available_volume": 5.0},
        ],
        "targets": {
            "N": {"target": 150.0, "abs_tolerance": 0.0},
            "P": {"target": 50.0, "abs_tolerance": 0.0},
            "K": {"target": 210.0, "abs_tolerance": 0.0},
            "Ca": {"target": 200.0, "abs_tolerance": 0.0},
            "Mg": {"target": 40.0, "abs_tolerance": 0.0},
            "Fe": {"target": 2.0, "abs_tolerance": 0.0},
        },
        "target_volume": 100.0,
        "max_deviation": 0.10,
    }
    r = solve(req)
    assert r["status"] == "optimal", r
    assert abs(r["total_stock_volume"] + r["dilution_water"] - 100.0) < 1e-6
    ach = r["achieved_concentrations"]
    for e, t in [("N", 150), ("P", 50), ("K", 210), ("Ca", 200), ("Mg", 40), ("Fe", 2)]:
        assert abs(ach[e] - t) / t <= 0.10, (e, ach)
    print("case_feasible OK:", {a["stock"]: a["volume"] for a in r["allocations"]},
          "water=", r["dilution_water"], "dev=", r["max_relative_deviation"])


def case_nitrogen_shortfall():
    """A 液可用体积太小,N 无法达标。"""
    req = {
        "stocks": [
            {"name": "A", "concentrations": {"N": 5000.0, "Ca": 10000.0},
             "available_volume": 0.5},
            {"name": "B", "concentrations": {"P": 3000.0, "K": 15000.0},
             "available_volume": 5.0},
        ],
        "targets": {
            "N": {"target": 150.0, "abs_tolerance": 0.0},
            "P": {"target": 50.0, "abs_tolerance": 0.0},
            "K": {"target": 210.0, "abs_tolerance": 0.0},
            "Ca": {"target": 200.0, "abs_tolerance": 0.0},
        },
        "target_volume": 100.0,
        "max_deviation": 0.05,
    }
    r = solve(req)
    assert r["status"] == "infeasible", r
    elems = {v["element"]: v for v in r["violations"]}
    assert "N" in elems and elems["N"]["direction"] == "shortfall"
    assert any("上限" in reason or "用尽" in reason for reason in elems["N"]["reasons"])
    print("case_nitrogen_shortfall OK: N reasons ->", elems["N"]["reasons"])


def case_forced_excess():
    """目标体积大,母液总量必须投很多;某元素只有高浓度母液提供 => 被迫过量。"""
    req = {
        "stocks": [
            {"name": "A", "concentrations": {"N": 20000.0}, "available_volume": 10.0},
            {"name": "B", "concentrations": {"K": 10000.0}, "available_volume": 10.0},
        ],
        "targets": {
            "N": {"target": 50.0, "abs_tolerance": 0.0},
            "K": {"target": 50.0, "abs_tolerance": 0.0},
        },
        "target_volume": 100.0,
        "max_deviation": 0.05,
        # 稀释水最多 99L => 母液至少投 1L;但 N、K 精确需求合计仅 0.75L,
        # 多投的母液必然使 N 或 K 过量
        "water_max": 99.0,
    }
    r = solve(req)
    # water_min=98 => 母液只能投 2L;N 要 0.25L K 要 0.5L,共 0.75L < 2L,
    # 被迫多投母液导致某元素过量
    assert r["status"] == "infeasible", r
    dirs = {v["element"]: v["direction"] for v in r["violations"]}
    assert "N" in dirs or "K" in dirs
    print("case_forced_excess OK:", dirs)
    for v in r["violations"]:
        print("  ", v["element"], v["direction"], v["reasons"])


def case_water_capacity():
    """母液+水上限都不够目标体积。"""
    req = {
        "stocks": [
            {"name": "A", "concentrations": {"N": 1000.0}, "available_volume": 1.0},
        ],
        "targets": {"N": {"target": 10.0, "abs_tolerance": 0.0}},
        "target_volume": 100.0,
        "max_deviation": 0.05,
        "water_max": 50.0,
    }
    r = solve(req)
    assert r["status"] == "infeasible", r
    elems = {v["element"]: v for v in r["violations"]}
    assert "__volume__" in elems or "N" in elems
    print("case_water_capacity OK:", [v["reasons"] for v in r["violations"]])


def case_zero_target_hard_excess():
    """Na 目标为 0、容差 0.5;water_max 强制母液至少投 1L,
    而唯一母液含 Na 100mg/L => 至少带入 1 mg/L,硬超标。"""
    req = {
        "stocks": [
            {"name": "A", "concentrations": {"N": 5000.0, "Na": 100.0},
             "available_volume": 10.0},
        ],
        "targets": {
            "N": {"target": 50.0, "abs_tolerance": 0.0},
            "Na": {"target": 0.0, "abs_tolerance": 0.5},
        },
        "target_volume": 100.0,
        "max_deviation": 0.05,
        "water_max": 99.0,
    }
    r = solve(req)
    assert r["status"] == "infeasible", r
    elems = {v["element"]: v for v in r["violations"]}
    assert "Na" in elems and elems["Na"]["direction"] == "excess"
    assert any("稀释水上限" in x for x in elems["Na"]["reasons"])
    print("case_zero_target_hard_excess OK:", elems["Na"]["reasons"])


if __name__ == "__main__":
    case_feasible()
    case_nitrogen_shortfall()
    case_forced_excess()
    case_water_capacity()
    case_zero_target_hard_excess()
    print("ALL SOLVER TESTS PASSED")
