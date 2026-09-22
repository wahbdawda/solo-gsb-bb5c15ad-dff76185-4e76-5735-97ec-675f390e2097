"""API + 数据库集成测试(fastapi TestClient)。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp()
os.environ["NUTRIENT_DB"] = os.path.join(_tmp, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)

FEASIBLE = {
    "name": "生菜通用配方",
    "stocks": [
        {"name": "A钙氮液",
         "concentrations": {"N": 10000.0, "Ca": 13334.0, "Fe": 134.0},
         "available_volume": 5.0},
        {"name": "B磷钾液",
         "concentrations": {"P": 3572.0, "K": 15000.0, "Mg": 2858.0},
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
    "max_deviation": 0.05,
}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    print("test_health OK")


def test_solve_feasible():
    r = client.post("/api/solve", json=FEASIBLE)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["feasible"] is True
    assert data["status"] == "optimal"
    assert abs(data["total_stock_volume"] + data["dilution_water"] - 100.0) < 1e-4
    print("test_solve_feasible OK, dev =", data["max_relative_deviation"])


def test_solve_infeasible_detail():
    bad = json.loads(json.dumps(FEASIBLE))
    bad["stocks"][0]["available_volume"] = 0.5  # N/Ca/Fe 严重受限
    bad.pop("name")
    r = client.post("/api/solve", json=bad)
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is False
    elems = {v["element"]: v for v in data["violations"]}
    assert "N" in elems and elems["N"]["direction"] == "shortfall"
    assert any("上限" in x for x in elems["N"]["reasons"])
    print("test_solve_infeasible_detail OK:",
          [(v["element"], v["direction"]) for v in data["violations"]])


def test_zero_target_trace():
    """Na 目标为 0(容差 0.5 mg/L),但供 N 的母液含 Na 比例过高,
    为达到 N 目标必然带入超标的 Na => 过量诊断并指出耦合来源。"""
    payload = json.loads(json.dumps(FEASIBLE))
    # 投 A 约 1.5L 时 Na = 200000*1.5/100 = 3 mg/L > 0.5 容差
    payload["stocks"][0]["concentrations"]["Na"] = 200000.0
    payload["targets"]["Na"] = {"target": 0.0, "abs_tolerance": 0.5}
    r = client.post("/api/solve", json=payload)
    data = r.json()
    assert data["feasible"] is False
    elems = {v["element"]: v for v in data["violations"]}
    assert "Na" in elems
    assert elems["Na"]["direction"] in ("excess", "binding")
    print("test_zero_target_trace OK: Na =", elems["Na"]["direction"],
          elems["Na"]["reasons"])


def test_validation_error():
    r = client.post("/api/solve", json={"stocks": [], "targets": {},
                                        "target_volume": 100})
    assert r.status_code == 422
    print("test_validation_error OK")


def test_save_versions_and_export():
    # 保存 v1
    r = client.post("/api/recipes/save?note=初版", json=FEASIBLE)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1

    # 修改后保存 v2
    v2 = json.loads(json.dumps(FEASIBLE))
    v2["targets"]["N"]["target"] = 160.0
    r = client.post("/api/recipes/save", json=v2)
    assert r.json()["version"] == 2

    # 列表
    r = client.get("/api/recipes")
    names = [x["name"] for x in r.json()]
    assert "生菜通用配方" in names

    # 默认取最新版
    r = client.get("/api/recipes/生菜通用配方")
    assert r.json()["version"] == 2

    # 指定版本
    r = client.get("/api/recipes/生菜通用配方?version=1")
    assert r.json()["version"] == 1

    # 404
    assert client.get("/api/recipes/不存在").status_code == 404

    # 导出 JSON(带 Content-Disposition)
    r = client.get("/api/recipes/生菜通用配方/export?version=1")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    exported = r.json()
    assert exported["request"]["target_volume"] == 100.0
    assert exported["result"]["feasible"] is True

    # 保存必须有 name
    noname = json.loads(json.dumps(FEASIBLE))
    noname.pop("name")
    assert client.post("/api/recipes/save", json=noname).status_code == 422
    print("test_save_versions_and_export OK")


if __name__ == "__main__":
    test_health()
    test_solve_feasible()
    test_solve_infeasible_detail()
    test_zero_target_trace()
    test_validation_error()
    test_save_versions_and_export()
    print("ALL API TESTS PASSED")
