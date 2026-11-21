# 多维分析查询与指标平台

面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/layout_imports.py  布局导入/核验/订阅日报接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/layout_import.py  跨域布局导入与指标绑定核验
│   ├── app/services/base.py     共享服务骨架
│   ├── app/layout_store.py      导入批次/绑定/订阅/投递的进程内仓库
│   ├── app/store.py          内存数据仓库与示例数据
│   ├── samples/revenue_layout.json  含两张卡片的示例布局文件
│   └── tests/                领域服务回归用例
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

### 布局导入（跨业务域）

原来的看板布局只能在所属业务域内消费，导出的布局文件也不带指标绑定。
`布局导入` 页面（`/layout-import`）用于把一个业务域的看板布局搬到另一个业务域：

1. **导入**：上传 JSON/YAML 布局文件（或点「载入示例布局」读取
   `backend/samples/revenue_layout.json`）。解析卡片位置、指标名称、来源域口径和示例数据。
2. **核验**：按目标域指标重新比对粒度、聚合函数和口径版本，输出待核项。
   文件里自带的 `confirmed` 等「已确认」声明一律不作为核验依据，只留作审计记录；
   同名指标也不会因为名字相同就算确认，唯一认可的自动确认依据是目标域核验台账
   （活跃设备数此前已核验，可直接复用）。
3. **核准**：对销售额这类粒度不一致的绑定，必须按目标域指标的真实粒度人工核准
   （目标域销售额按小时，核准成「按日」会被拒绝），核准结果写入核验台账。
4. **订阅日报**：导入时自动创建订阅。日报只发布已核验的数值，未核验卡片保留行、
   数值留空并标注「待核不发布」和待核原因；每份日报都保存实际采用的绑定快照
   （`binding_id` / 粒度 / 聚合函数 / 口径版本）。
5. **对比与重算**：同一导入的两次投递可以逐卡对比数值和待核项；口径变更会把使用该
   指标的绑定标记为待复核，`caliber-recalc` 按 `binding_id` 列出重算值和引用过它的日报。

```bash
# 导入示例布局（会自动创建订阅）
curl -X POST http://127.0.0.1:8000/api/layout-imports -H 'Content-Type: application/json' -d '{}'
# 发送一次日报
curl -X POST http://127.0.0.1:8000/api/layout-imports/1/send -H 'Content-Type: application/json' -d '{}'
# 核验销售额绑定为「按小时」
curl -X POST http://127.0.0.1:8000/api/layout-imports/1/bindings/BD-0001/confirm \
  -H 'Content-Type: application/json' -d '{"granularity":"按小时"}'
```

主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/layout-imports` | 导入记录列表 |
| POST | `/api/layout-imports` | 导入布局（JSON 请求体、纯文本文件内容或 `path`） |
| GET | `/api/layout-imports/{id}` | 导入详情：卡片、绑定、待核项、订阅、投递 |
| POST | `/api/layout-imports/{id}/bindings/{binding_id}/confirm` | 按目标域粒度核准绑定 |
| POST | `/api/layout-imports/{id}/send` | 按默认订阅发送日报 |
| GET | `/api/layout-imports/deliveries/compare?left=&right=` | 对比两次日报的数值与待核项 |
| POST | `/api/layout-imports/caliber-changes` | 变更指标口径并标记受影响绑定 |
| GET | `/api/layout-imports/caliber-recalc?metric_code=` | 按绑定重算并定位使用过的日报 |

后端回归用例：`cd backend && .venv/bin/python -m unittest discover -s tests -v`

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编码、指标名称、业务域 |
| 多维立方 | `cube_builder` | OLAP立方 | 立方名称、事实表、维度列表 |
| 切片查询 | `slice_query` | 查询模板 | 模板名称、数据立方、查询维值 |
| 分群圈选 | `segment_rule` | 分群规则 | 分群名称、人群类型、筛选条件 |
| 看板布局 | `dashboard_layout` | 看板配置 | 看板名称、所属业务域、图表布局 |
| 即席导出 | `adhoc_export` | 导出任务 | 任务名称、查询SQL、导出格式 |
| 报表订阅 | `report_subscription` | 订阅配置 | 订阅名称、报表对象、推送周期 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则名称、监控指标、检测算法 |
| 下钻路径 | `drill_path` | 下钻配置 | 路径名称、起始维度、下钻层级 |
| 语义层 | `semantic_layer` | 语义定义 | 语义名称、物理表、字段映射 |
| 查询加速 | `query_accelerator` | 加速配置 | 配置名称、目标查询、加速方式 |
| 归因模型 | `attribution_model` | 归因配置 | 模型名称、归因对象、归因窗口 |
| 同期群分析 | `cohort_analysis` | 群组配置 | 群组名称、初始事件、回访事件 |
| 数据血缘 | `data_lineage` | 血缘节点 | 节点名称、节点类型、上游列表 |
| 查询调度 | `query_scheduler` | 调度计划 | 计划名称、查询语句、执行周期 |
| 结果缓存 | `result_cache` | 缓存条目 | 缓存键、查询描述、结果行数 |
| 数据范围 | `olap_scope` | 范围规则 | 规则名称、数据范围、授权对象 |
| 实验分析 | `ab_analysis` | 实验配置 | 实验名称、对照组、实验组 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
