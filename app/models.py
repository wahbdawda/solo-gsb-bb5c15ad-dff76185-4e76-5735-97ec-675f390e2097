"""Pydantic models: 请求/响应数据结构。

单位约定:
- 体积 volume / available_volume / target_volume: 升 (L)
- 浓度 concentrations / 目标 target: 毫克每升 (mg/L, 以元素单质计)
- 投料量 v: 升, 混合液中某元素总量 = sum(concentration_e * v),
  最终浓度 = 总量 / target_volume。
"""
from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator


class StockSolution(BaseModel):
    """一种母液(浓缩液)。"""

    name: str = Field(..., min_length=1, description="母液名称,如 A 液(钙盐)")
    concentrations: Dict[str, float] = Field(
        ...,
        description='各元素浓度 mg/L,键为元素符号如 "N"/"P"/"K"/"Ca"/"Fe"',
    )
    available_volume: float = Field(..., gt=0, description="可用体积上限,升")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("母液名称不能为空")
        return v

    @field_validator("concentrations")
    @classmethod
    def _non_negative(cls, v: Dict[str, float]) -> Dict[str, float]:
        cleaned: Dict[str, float] = {}
        for key, val in v.items():
            key = key.strip()
            if not key:
                raise ValueError("元素符号不能为空")
            if val < 0:
                raise ValueError(f"元素 {key} 的浓度不能为负")
            cleaned[key] = float(val)
        return cleaned


class ElementTarget(BaseModel):
    """某元素的目标浓度与绝对容差。"""

    target: float = Field(..., ge=0, description="目标浓度 mg/L")
    abs_tolerance: float = Field(
        0.0,
        ge=0,
        description="绝对容差 mg/L;target=0 的元素默认 1.0",
    )


class SolveRequest(BaseModel):
    """试算/保存接口的求解请求。"""

    name: Optional[str] = Field(None, description="配方名称(保存时使用)")
    stocks: list[StockSolution] = Field(..., min_length=1)
    targets: Dict[str, ElementTarget] = Field(..., min_length=1)
    target_volume: float = Field(..., gt=0, description="目标配方总体积,升")
    max_deviation: float = Field(
        0.05,
        gt=0,
        description="可接受的最大相对偏差,默认 5%%;target=0 的元素用绝对容差判定",
    )
    water_min: Optional[float] = Field(
        0.0, ge=0, description="稀释水体积下限,升(默认 0)"
    )
    water_max: Optional[float] = Field(
        None, ge=0, description="稀释水体积上限,升(默认不限)"
    )


class Allocation(BaseModel):
    stock: str
    volume: float
    available_volume: float
    volume_utilization: float


class Violation(BaseModel):
    element: str
    direction: str  # "shortfall" | "excess" | "binding"(零目标元素贴容差起约束)
    target: float
    achieved: float
    relative_error: float
    tolerance: float
    reasons: list[str]


class SolveResult(BaseModel):
    status: str  # "optimal" | "infeasible" | "solver_error"
    feasible: bool
    max_relative_deviation: Optional[float] = None
    total_stock_volume: Optional[float] = None
    dilution_water: Optional[float] = None
    water_min: float
    water_max: Optional[float]
    allocations: list[Allocation] = []
    achieved_concentrations: Dict[str, float] = {}
    violations: list[Violation] = []
    message: str
