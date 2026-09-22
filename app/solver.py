"""配比求解器。

变量 x = [v_1..v_m, d],其中 v_i 为第 i 种母液的投料体积(升),d 为最大相对偏差。

    minimize d

    元素约束(对每个目标元素 e,目标浓度 t_e,配液体积 V):
        (1-d) t_e V <= sum_i c_{i,e} v_i <= (1+d) t_e V
        t_e = 0 时: sum_i c_{i,e} v_i <= abs_tol_e * V

    总量/稀释水约束(water = V - sum v):
        water_min <= water <= water_max
        => V - water_max <= sum_i v_i <= V - water_min
        另有母液总量不超过可用体积: sum_i v_i <= sum_i cap_i

    单液上限: 0 <= v_i <= cap_i

无可行解时,在"全部元素联合、偏差放宽到 100%"的 minimax 解上逐元素判定
不足/过量,并用只含体积约束的极值 LP 区分"单液上限硬限制"与"元素耦合牵制",
给出具体约束原因。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import linprog


def _norm_targets(targets: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
    out: Dict[str, Tuple[float, float]] = {}
    for elem, spec in targets.items():
        if isinstance(spec, dict):
            tgt = float(spec["target"])
            tol = float(spec.get("abs_tolerance", 0.0) or 0.0)
        else:
            tgt = float(spec.target)
            tol = float(spec.abs_tolerance)
        if tgt == 0.0 and tol == 0.0:
            tol = 1.0  # target=0 的元素默认 1 mg/L 绝对容差
        out[elem.strip()] = (tgt, tol)
    return out


def _norm_stocks(stocks: List[Any]):
    names, mats, caps = [], [], []
    for s in stocks:
        if isinstance(s, dict):
            names.append(s["name"].strip())
            mats.append({k.strip(): float(v) for k, v in s["concentrations"].items()})
            caps.append(float(s["available_volume"]))
        else:
            names.append(s.name.strip())
            mats.append(dict(s.concentrations))
            caps.append(float(s.available_volume))
    return names, mats, caps


def _linprog(c, A_ub, b_ub, bounds):
    return linprog(
        c=c,
        A_ub=np.array(A_ub, dtype=float) if A_ub else None,
        b_ub=np.array(b_ub, dtype=float) if b_ub else None,
        A_eq=None,
        b_eq=None,
        bounds=bounds,
        method="highs",
    )


def _base_bounds(m, caps):
    return [(0.0, caps[i]) for i in range(m)]


def _volume_constraints(m, target_volume, water_min, water_max):
    """母液总量 S=sum v 的可行区间 [V-water_max, V-water_min]。"""
    lo = target_volume - water_max if water_max is not None else 0.0
    hi = target_volume - water_min
    return lo, hi


def _build_matrix(names, mats, caps, targets, target_volume,
                  water_min, water_max, d_cap):
    """构造 minimax LP 的矩阵。变量 [v_1..v_m, d]。"""
    m = len(names)
    A, b = [], []
    lo, hi = _volume_constraints(m, target_volume, water_min, water_max)
    if hi < lo:
        return None, None, None
    # S <= hi
    A.append([1.0] * m + [0.0])
    b.append(hi)
    # -S <= -lo
    A.append([-1.0] * m + [0.0])
    b.append(-lo)

    for elem, (tgt, tol) in targets.items():
        ce = [mat.get(elem, 0.0) for mat in mats]
        if tgt > 0:
            T = tgt * target_volume
            A.append(ce + [-T])
            b.append(T)
            A.append([-c for c in ce] + [-T])
            b.append(-T)
        else:
            A.append(ce + [0.0])
            b.append(tol * target_volume)

    bounds = _base_bounds(m, caps) + [(0.0, d_cap)]
    c = [0.0] * m + [1.0]
    return c, A, b, bounds


def solve(payload: Dict[str, Any]) -> Dict[str, Any]:
    stocks = payload["stocks"]
    target_volume = float(payload["target_volume"])
    max_dev_allowed = float(payload.get("max_deviation", 0.05))
    water_min = float(payload.get("water_min") or 0.0)
    wm = payload.get("water_max")
    water_max: Optional[float] = float(wm) if wm is not None else None

    if water_min < 0:
        raise ValueError("water_min 不能为负")
    if water_max is not None and water_max < water_min:
        raise ValueError("water_max 不能小于 water_min")
    if water_min > target_volume:
        raise ValueError("water_min 不能大于目标配方体积")

    names, mats, caps = _norm_stocks(stocks)
    targets = _norm_targets(payload["targets"])
    m = len(names)
    elements = list(targets.keys())

    lo, hi = _volume_constraints(m, target_volume, water_min, water_max)

    # 纯体积容量预检
    cap_total = sum(caps)
    water_cap = water_max if water_max is not None else target_volume
    if cap_total + water_cap < target_volume - 1e-9:
        return _infeasible_payload(
            water_min, water_max, {},
            [_violation(
                "__volume__", "shortfall", target_volume,
                cap_total + water_cap, 0.0, 0.0,
                [f"母液可用体积合计 {cap_total:g} L + 稀释水可用上限 "
                 f"{water_cap:g} L = {cap_total + water_cap:g} L "
                 f"< 目标配方体积 {target_volume:g} L,液体总量不足以配满"],
            )],
        )
    if hi < -1e-9:
        return _infeasible_payload(
            water_min, water_max, {},
            [_violation(
                "__volume__", "excess", target_volume, hi, 0.0, 0.0,
                [f"稀释水下限 {water_min:g} L 高于目标体积,无可行投放方案"],
            )],
        )

    # 主 LP:允许 d 到 100%(d=1 时下带约束归零,等价于"尽力而为")
    built = _build_matrix(names, mats, caps, targets, target_volume,
                          water_min, water_max, 1.0)
    c, A, b, bounds = built
    res = _linprog(c, A, b, bounds)

    if not res.success:
        # d<=1 仍不可行:诊断
        return _diagnose(names, mats, caps, targets, target_volume,
                         water_min, water_max, max_dev_allowed)

    volumes = np.clip(res.x[:m], 0.0, None)
    d_opt = float(res.x[m])
    water = target_volume - float(sum(volumes))

    achieved = {
        elem: float(
            sum(mats[i].get(elem, 0.0) * volumes[i] for i in range(m))
            / target_volume
        )
        for elem in elements
    }

    violations = []
    for elem in elements:
        tgt, tol = targets[elem]
        got = achieved[elem]
        if tgt > 0:
            rel = abs(got - tgt) / tgt
            if rel > max_dev_allowed + 1e-7:
                direction = "shortfall" if got < tgt else "excess"
                hard_shortfall, max_conc = (False, got)
                if direction == "shortfall":
                    hard_shortfall, max_conc = _check_hard_shortfall(
                        mats, caps, elem, tgt, target_volume,
                        water_min, water_max, max_dev_allowed,
                    )
                    if hard_shortfall:
                        got = max_conc
                        achieved[elem] = got
                        rel = (tgt - max_conc) / tgt
                reasons = _optimal_reasons(
                    elem, direction, names, caps, mats, targets, volumes,
                    target_volume, water_min, water_max, max_dev_allowed,
                    hard_shortfall=hard_shortfall, max_conc=max_conc,
                )
                violations.append(_violation(
                    elem, direction, tgt, got, rel, max_dev_allowed, reasons,
                ))
        else:
            # 零目标元素:超过容差,或贴着容差起约束作用,都需要报告
            at_tolerance = abs(got - tol) <= 1e-7 + 1e-9 * max(tol, 1.0)
            if got > tol + 1e-7:
                reasons = _optimal_reasons(
                    elem, "excess", names, caps, mats, targets, volumes,
                    target_volume, water_min, water_max, max_dev_allowed,
                )
                violations.append(_violation(
                    elem, "excess", 0.0, got,
                    got / tol if tol > 0 else got, tol, reasons,
                ))
            elif at_tolerance and d_opt > max_dev_allowed + 1e-7:
                reasons = [
                    f"元素 {elem} 目标为 0,投入任何含该元素的母液都会带入它;"
                    f"最优方案中已贴着绝对容差 {tol:g} mg/L,成为限制其他元素"
                    "投料量的耦合约束"
                ]
                contributors = sorted(
                    ((i, mats[i].get(elem, 0.0) * volumes[i])
                     for i in range(m)
                     if volumes[i] * mats[i].get(elem, 0.0) > 1e-9),
                    key=lambda p: -p[1],
                )[:3]
                if contributors:
                    reasons.append(
                        "来源: "
                        + ", ".join(
                            f"「{names[i]}」{volumes[i]:.4g} L"
                            for i, _ in contributors
                        )
                    )
                violations.append(_violation(
                    elem, "binding", 0.0, got, 1.0, tol, reasons,
                ))

    allocations = [
        {
            "stock": names[i],
            "volume": round(float(volumes[i]), 6),
            "available_volume": caps[i],
            "volume_utilization": round(
                float(volumes[i] / caps[i]) if caps[i] > 0 else 0.0, 6
            ),
        }
        for i in range(m)
        if volumes[i] > 1e-7
    ]
    total_stock = float(sum(volumes))

    if violations:
        return {
            "status": "infeasible",
            "feasible": False,
            "max_relative_deviation": round(d_opt, 6),
            "total_stock_volume": round(total_stock, 6),
            "dilution_water": round(water, 6),
            "water_min": water_min,
            "water_max": water_max,
            "allocations": allocations,
            "achieved_concentrations": {k: round(v, 6) for k, v in achieved.items()},
            "violations": violations,
            "message": (
                f"在给定约束下无法把全部元素控制在允许偏差 "
                f"{max_dev_allowed:.2%} 内(最优最大相对偏差 "
                f"{d_opt:.2%});violations 给出了超限元素及约束原因。"
            ),
        }

    return {
        "status": "optimal",
        "feasible": True,
        "max_relative_deviation": round(d_opt, 6),
        "total_stock_volume": round(total_stock, 6),
        "dilution_water": round(water, 6),
        "water_min": water_min,
        "water_max": water_max,
        "allocations": allocations,
        "achieved_concentrations": {k: round(v, 6) for k, v in achieved.items()},
        "violations": [],
        "message": "求解成功。",
    }


def _violation(elem, direction, tgt, got, rel, tol, reasons):
    return {
        "element": elem,
        "direction": direction,
        "target": round(float(tgt), 6),
        "achieved": round(float(got), 6),
        "relative_error": round(float(rel), 6),
        "tolerance": round(float(tol), 6),
        "reasons": reasons,
    }


def _infeasible_payload(water_min, water_max, achieved_best, violations):
    return {
        "status": "infeasible",
        "feasible": False,
        "max_relative_deviation": None,
        "total_stock_volume": None,
        "dilution_water": None,
        "water_min": water_min,
        "water_max": water_max,
        "allocations": [],
        "achieved_concentrations": {k: round(v, 6) for k, v in achieved_best.items()},
        "violations": violations,
        "message": "无可行解:见 violations 中各超限元素与约束原因。",
    }


def _diagnose(names, mats, caps, targets, target_volume,
              water_min, water_max, max_dev_allowed):
    """主 LP 在 d<=1 下不可行时的诊断(通常是 volume 区间或元素硬冲突)。"""
    m = len(names)
    lo, hi = _volume_constraints(m, target_volume, water_min, water_max)

    # 仅体积约束下的极值 LP
    A_vol = [[1.0] * m], [hi], [-1.0] * m, [-lo]
    A, b = [], []
    A.append([1.0] * m)
    b.append(hi)
    A.append([-1.0] * m)
    b.append(-lo)
    bounds = _base_bounds(m, caps)

    violations = []
    achieved_best: Dict[str, float] = {}

    for elem, (tgt, tol) in targets.items():
        ce = [mat.get(elem, 0.0) for mat in mats]
        # 最大可达
        rmax = _linprog([-c for c in ce], A, b, bounds)
        max_conc = float(-rmax.fun) / target_volume if rmax.success else 0.0
        achieved_best[elem] = max_conc

        if tgt > 0:
            if max_conc + 1e-7 < tgt * (1 - max_dev_allowed):
                vol = np.clip(rmax.x[:m], 0, None) if rmax.success else None
                reasons = _hard_shortfall_reasons(
                    names, caps, ce, elem, tgt, max_conc, target_volume,
                    max_dev_allowed, vol,
                )
                violations.append(_violation(
                    elem, "shortfall", tgt, max_conc,
                    (tgt - max_conc) / tgt, max_dev_allowed, reasons,
                ))
                continue

            # 最小可达(在体积区间约束下)
            rmin = _linprog(ce, A, b, bounds)
            min_conc = float(rmin.fun) / target_volume if rmin.success else 0.0
            if min_conc > tgt * (1 + max_dev_allowed) + 1e-7:
                vol = np.clip(rmin.x[:m], 0, None) if rmin.success else None
                reasons = _forced_excess_reasons(
                    names, caps, mats, ce, elem, min_conc, target_volume,
                    water_min, water_max, vol,
                )
                violations.append(_violation(
                    elem, "excess", tgt, min_conc,
                    (min_conc - tgt) / tgt, max_dev_allowed, reasons,
                ))
                achieved_best[elem] = min_conc
        else:
            rmin = _linprog(ce, A, b, bounds)
            min_conc = float(rmin.fun) / target_volume if rmin.success else 0.0
            if min_conc > tol + 1e-7:
                vol = np.clip(rmin.x[:m], 0, None) if rmin.success else None
                reasons = _forced_excess_reasons(
                    names, caps, mats, ce, elem, min_conc, target_volume,
                    water_min, water_max, vol,
                )
                violations.append(_violation(
                    elem, "excess", 0.0, min_conc,
                    min_conc / tol if tol > 0 else min_conc, tol, reasons,
                ))
                achieved_best[elem] = min_conc

    return _infeasible_payload(water_min, water_max, achieved_best, violations)


def _check_hard_shortfall(mats, caps, elem, tgt, target_volume,
                          water_min, water_max, max_dev_allowed):
    """忽略其他元素,只在体积/单液上限约束下求该元素最大可达浓度。"""
    m = len(mats)
    lo, hi = _volume_constraints(m, target_volume, water_min, water_max)
    ce = [mat.get(elem, 0.0) for mat in mats]
    A = [[1.0] * m, [-1.0] * m]
    b = [hi, -lo]
    r = _linprog([-c for c in ce], A, b, _base_bounds(m, caps))
    if not r.success:
        return False, 0.0
    max_conc = float(-r.fun) / target_volume
    return max_conc + 1e-7 < tgt * (1 - max_dev_allowed), max_conc


def _binding(i, v, caps, scale, names):
    cap = caps[i]
    if cap > 0 and v >= cap - max(1e-7, 1e-6 * scale):
        return f"母液「{names[i]}」已达可用体积上限 {cap:g} L"
    return None


def _hard_shortfall_reasons(names, caps, ce, elem, tgt, max_conc,
                            target_volume, max_dev_allowed, vol):
    reasons = []
    providers = [i for i in range(len(names)) if ce[i] > 0]
    if not providers:
        return [f"所有母液均不含元素 {elem},无法提供目标 {tgt:g} mg/L"]
    if vol is not None:
        scale = max(float(np.max(vol)), 1.0)
        for i in providers:
            desc = _binding(i, vol[i], caps, scale, names)
            if desc:
                reasons.append(desc)
    cap_total = sum(ce[i] * caps[i] for i in providers)
    top = ", ".join(
        f"{names[i]}({ce[i]:g} mg/L × {caps[i]:g} L)"
        for i in sorted(providers, key=lambda i: -ce[i])[:3]
    )
    reasons.append(
        f"含 {elem} 母液全部按上限投放也只能达到 {max_conc:.4g} mg/L"
        f"(理论总量 {cap_total:.4g} mg ÷ {target_volume:g} L),"
        f"低于目标下限 {tgt * (1 - max_dev_allowed):.4g} mg/L"
    )
    reasons.append(f"主要供 {elem} 母液: {top}")
    return reasons


def _forced_excess_reasons(names, caps, mats, ce, elem, conc,
                           target_volume, water_min, water_max, vol):
    reasons = []
    m = len(names)
    if vol is not None:
        scale = max(float(np.max(vol)), 1.0)
        for i in range(m):
            desc = _binding(i, vol[i], caps, scale, names)
            if desc and ce[i] > 0:
                reasons.append(desc)
        contribs = sorted(
            ((i, ce[i] * vol[i]) for i in range(m) if vol[i] * ce[i] > 1e-9),
            key=lambda p: -p[1],
        )[:3]
        if contribs:
            reasons.append(
                "体积区间约束下被迫投入: "
                + ", ".join(
                    f"「{names[i]}」{vol[i]:.4g} L(带入 {elem} "
                    f"{amt / target_volume:.4g} mg/L)"
                    for i, amt in contribs
                )
            )
    if water_max is not None:
        reasons.append(
            f"稀释水上限 {water_max:g} L 要求母液至少投放 "
            f"{target_volume - water_max:g} L"
        )
    reasons.append(
        f"该约束下 {elem} 最低仍有 {conc:.4g} mg/L,超过目标上限"
    )
    return reasons


def _optimal_reasons(elem, direction, names, caps, mats, targets, volumes,
                     target_volume, water_min, water_max, max_dev_allowed,
                     hard_shortfall=False, max_conc=None):
    """基于 minimax 最优解解释某元素超限的元素耦合/约束原因。"""
    m = len(names)
    ce = [mat.get(elem, 0.0) for mat in mats]
    scale = max(float(np.max(volumes)) if len(volumes) else 1.0, 1.0)
    near = lambda v, cap: cap > 0 and v >= cap - max(1e-7, 1e-6 * scale)

    if direction == "shortfall":
        providers = [i for i in range(m) if ce[i] > 0]
        if not providers:
            return [f"所有母液均不含元素 {elem}"]
        if hard_shortfall:
            return _hard_shortfall_reasons(
                names, caps, ce, elem, targets[elem][0], max_conc,
                target_volume, max_dev_allowed, volumes,
            )
        reasons = []
        capped = [i for i in providers if near(volumes[i], caps[i])]
        if capped:
            reasons.append(
                "供应该元素的母液已达可用体积上限: "
                + ", ".join(
                    f"「{names[i]}」({caps[i]:g} L,含 {elem} {ce[i]:g} mg/L)"
                    for i in capped
                )
            )
        coupled = sorted({
            e for i in capped for e in mats[i]
            if e != elem and targets.get(e, (0,))[0] > 0
        })
        if coupled:
            reasons.append(
                f"母液同时携带元素 {'、'.join(coupled)},投放量受其上限约束牵制"
            )
        reasons.append(
            f"权衡全部元素后 {elem} 最优仅 {max_conc:.4g} mg/L,"
            f"低于目标下限 {targets[elem][0] * (1 - max_dev_allowed):.4g} mg/L"
        )
        return reasons

    # excess:元素耦合解释
    reasons = []
    contributors = sorted(
        ((i, ce[i] * volumes[i]) for i in range(m) if volumes[i] * ce[i] > 1e-9),
        key=lambda p: -p[1],
    )
    for i, amount in contributors[:3]:
        forced_by = []
        for other, (otgt, _) in targets.items():
            if other == elem or otgt <= 0:
                continue
            total_other = sum(
                mats[k].get(other, 0.0) * volumes[k] for k in range(m)
            )
            share = (
                mats[i].get(other, 0.0) * volumes[i] / total_other
                if total_other > 1e-12 else 0.0
            )
            if share > 0.5:
                forced_by.append(f"{other}(占其来源 {share:.0%})")
        parts = (
            f"「{names[i]}」投入 {volumes[i]:.4g} L,带入 {elem} "
            f"{amount / target_volume:.4g} mg/L"
        )
        if forced_by:
            parts += "——为满足元素 " + "、".join(forced_by) + " 必须投入"
        elif near(volumes[i], caps[i]):
            parts += "——该母液已达可用体积上限"
        reasons.append(parts)
    if water_max is not None:
        reasons.append(
            f"稀释水上限 {water_max:g} L 要求母液至少投放 "
            f"{max(0.0, target_volume - water_max):g} L"
        )
    reasons.append(
        f"元素 {elem} 与来源母液中的其他元素耦合,偏差权衡后仍高于目标上限;"
        "需更换该元素含量更低的母液或放宽容差"
    )
    return reasons
