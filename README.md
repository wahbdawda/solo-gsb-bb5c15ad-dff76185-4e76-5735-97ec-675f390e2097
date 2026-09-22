# 水培营养液配比求解 API

给定若干**浓缩母液**的元素浓度（N/P/K 及 Ca、Mg、Fe 等微量元素）、各母液的
**可用体积上限**和**目标配方体积**，用线性规划（SciPy/HiGHS）求各母液的投料量与
稀释水量，目标是**最小化各元素实际浓度相对目标浓度的最大相对偏差**，同时满足：

- 总量约束：母液投料总量 + 稀释水 = 目标配方体积；
- 单液上限：每种母液投料量 ≤ 其可用体积；
- 稀释水约束：可选的 `water_min` / `water_max`（升）。

无可行解（或无法在允许偏差内满足全部目标）时，响应中逐个列出超限元素、方向
（`shortfall` 不足 / `excess` 过量 / `binding` 零目标元素贴容差起约束）、可达浓度
和**具体约束原因**（哪种母液达到体积上限、与哪些元素耦合、水量约束等）。

## 单位约定

| 量 | 单位 |
|---|---|
| 体积 `available_volume` / `target_volume` / `water_min/max` | 升 L |
| 母液浓度 `concentrations` / 目标 `target` / 容差 `abs_tolerance` | mg/L（以元素单质计，如 N、P、K、Ca、Mg、Fe） |

混合后某元素最终浓度 = Σ(母液浓度 × 投料体积) / 目标配方体积。

## 一条命令启动（Docker Compose）

```bash
docker compose up --build
```

- 监听端口：**8000**
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- SQLite 数据库文件：容器内 `/data/nutrient.db`，通过命名卷 `nutrient-data` 持久化；
  **首次启动自动建表**（`recipes`、`recipe_versions`）。

本地非 Docker 运行：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/solve` | 试算（只求解，不落库） |
| POST | `/api/recipes/save?note=...` | 求解并以 `name` 保存为新版本（同名自动 +1） |
| GET  | `/api/recipes` | 配方列表（含版本数、最新版本号） |
| GET  | `/api/recipes/{name}?version=N` | 查询某配方（默认最新版本） |
| GET  | `/api/recipes/{name}/export?version=N` | 导出该版本 JSON（attachment 下载） |
| GET  | `/health` | 健康检查 |

## curl 示例

### 1) 试算（可行配方）

```bash
curl -s http://localhost:8000/api/solve \
  -H 'Content-Type: application/json' \
  -d @examples/lettuce.json
```

返回（节选）：

```json
{
  "status": "optimal",
  "feasible": true,
  "max_relative_deviation": 0.0025,
  "total_stock_volume": 2.8928,
  "dilution_water": 97.1072,
  "water_min": 0.0,
  "water_max": null,
  "allocations": [
    {"stock": "A钙氮液(硝酸钙母液)", "volume": 1.4963, "available_volume": 5.0,
     "volume_utilization": 0.2993},
    {"stock": "B磷钾液(磷酸二氢钾+硫酸镁母液)", "volume": 1.3965,
     "available_volume": 5.0, "volume_utilization": 0.2793}
  ],
  "achieved_concentrations": {"N": 149.63, "P": 49.88, "K": 209.48,
                              "Ca": 199.51, "Mg": 39.91, "Fe": 2.01},
  "violations": [],
  "message": "求解成功。"
}
```

也可不借助文件直接内联提交：

```bash
curl -s http://localhost:8000/api/solve -H 'Content-Type: application/json' -d '{
  "stocks": [
    {"name": "A", "concentrations": {"N": 10000, "Ca": 13334, "Fe": 134},
     "available_volume": 5},
    {"name": "B", "concentrations": {"P": 3572, "K": 15000, "Mg": 2858},
     "available_volume": 5}
  ],
  "targets": {"N": {"target": 150}, "P": {"target": 50}, "K": {"target": 210},
              "Ca": {"target": 200}, "Mg": {"target": 40}, "Fe": {"target": 2}},
  "target_volume": 100,
  "max_deviation": 0.05
}'
```

### 2) 无可行解：返回超限元素与约束原因

```bash
curl -s http://localhost:8000/api/solve \
  -H 'Content-Type: application/json' \
  -d @examples/infeasible_nitrogen.json
```

返回（节选）：

```json
{
  "status": "infeasible",
  "feasible": false,
  "violations": [
    {
      "element": "N",
      "direction": "shortfall",
      "target": 150.0,
      "achieved": 25.0,
      "relative_error": 0.8333,
      "tolerance": 0.05,
      "reasons": [
        "母液「A液」已达可用体积上限 0.5 L",
        "含 N 母液全部按上限投放也只能达到 25 mg/L(理论总量 2500 mg ÷ 100 L),低于目标下限 142.5 mg/L",
        "主要供 N 母液: A液(5000 mg/L × 0.5 L)"
      ]
    }
  ],
  "message": "无可行解:见 violations 中各超限元素与约束原因。"
}
```

过量场景（例如设了 `water_max` 迫使母液多投）会给出元素耦合原因，形如：
`「A」投入 0.33 L,带入 N 66.67 mg/L——为满足元素 ... 必须投入`。

### 3) 保存配方版本

```bash
curl -s -X POST "http://localhost:8000/api/recipes/save?note=v1" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/lettuce.json
# => {"recipe_id":1,"name":"生菜通用配方","version":1,"feasible":true,...}
```

再次用同名 `name` 提交（即使不可行也可存档对比）会自动存为 `version: 2`。

### 4) 列表 / 查询版本

```bash
curl -s http://localhost:8000/api/recipes | jq .
curl -s "http://localhost:8000/api/recipes/生菜通用配方"            # 最新版
curl -s "http://localhost:8000/api/recipes/生菜通用配方?version=1"  # 指定版本
```

### 5) JSON 导出

```bash
curl -s -OJ "http://localhost:8000/api/recipes/生菜通用配方/export?version=1"
# 保存为 生菜通用配方-v1.json
```

## 请求字段说明

```jsonc
{
  "name": "配方名称(仅保存时必填)",
  "stocks": [
    {
      "name": "母液名称",
      "concentrations": {"N": 10000.0, "Fe": 134.0},  // mg/L,键为元素符号
      "available_volume": 5.0                          // 可用体积上限,L(>0)
    }
  ],
  "targets": {
    "N":  {"target": 150.0, "abs_tolerance": 0.0},  // 目标 mg/L
    "Na": {"target": 0.0,   "abs_tolerance": 0.5}   // target=0:不希望出现,
                                                    // 用绝对容差 mg/L 判定
  },
  "target_volume": 100.0,  // 配液总体积 L
  "max_deviation": 0.05,   // 可接受最大相对偏差,默认 5%
  "water_min": 0.0,        // 可选,稀释水下限 L
  "water_max": 90.0        // 可选,稀释水上限 L(限水时母液必须多投)
}
```

- `target` 为正的元素按相对偏差判定：`|实际−目标|/目标 ≤ max_deviation`；
- `target = 0` 的元素（禁用/杂质元素）按 `abs_tolerance`（默认 1 mg/L）绝对判定；
- 元素键可任取（N、P、K、Ca、Mg、S、Fe、Mn、Zn、B、Cu、Mo…），求解器按提交的
  键统一处理，未出现在 `targets` 中的元素不参与优化。

## 数学模型

变量 `v_i`（各母液投料体积，L）、`d`（最大相对偏差），`c_{i,e}` 为母液 i 中元素 e
的浓度，`t_e` 为目标浓度，`V` 为目标体积：

```
minimize d

(1−d)·t_e·V ≤ Σ_i c_{i,e}·v_i ≤ (1+d)·t_e·V      (每个目标元素)
             Σ_i c_{i,e}·v_i ≤ abs_tol_e·V        (target=0 的元素)
V−water_max ≤ Σ_i v_i ≤ V−water_min               (总量/稀释水)
0 ≤ v_i ≤ available_volume_i                       (单液上限)
```

这是关于 `v_i`、`d` 的线性规划，由 SciPy `linprog(method="highs")` 求解。

## 测试

```bash
python tests/test_solver.py   # 可行/不足/被迫过量/水量不足/零目标硬超标
python tests/test_api.py      # 试算、不可行诊断、版本保存、查询、JSON 导出
```

## 项目结构

```
app/
  main.py       FastAPI 路由(lifespan 启动建表)
  solver.py     minimax 线性规划 + 无可行解诊断
  models.py     Pydantic 请求/响应模型
  database.py   SQLite 初始化与配方版本存取
examples/       可行与不可行示例请求
tests/          求解器与 API 测试
Dockerfile, docker-compose.yml
```
