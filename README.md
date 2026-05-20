# Ozon 跨境电商 AI 选品系统

一个面向 Ozon 跨境电商场景的 AI 选品项目，当前主链路聚焦于：

- 使用 `Playwright` + 上品帮插件采集 Ozon 搜索结果与详情数据
- 对 Ozon 商品做红线筛选、评分、图片下载和属性补抓
- 支持从上品帮热销页自动提取上一级/上两级关键词，再驱动 Ozon 采集
- 使用人工审核完成的 Ozon 主图在 `1688` 浏览器端执行以图搜图
- 先做 GPT 主图相似度比对，每个 Ozon 商品只保留分数最高的一条 1688 结果
- 只对主图相似度最高的结果抓取 1688 详情页参数
- 采集结果默认写入 `SQLite`，再通过 Web 页面管理和导出 `XLSX`

## 技术栈

- 后端采集：Python 3.11+ / Playwright
- AI 比对：OpenAI GPT-5.4
- 供应链数据：1688 浏览器图搜图
- 数据存储：SQLite、Supabase
- 审核面板：Streamlit
- 调度系统：APScheduler

## 项目目录

```text
select_product/
├── .env.example
├── README.md
├── requirements.txt
├── config/
│   ├── __init__.py
│   └── settings.py
├── data/
│   ├── exports/
│   │   └── .gitkeep
│   ├── processed/
│   │   └── .gitkeep
│   └── raw/
│       └── .gitkeep
├── docs/
│   ├── architecture.md
│   ├── sqlite_schema.sql
│   └── supabase_schema.sql
├── extensions/
│   └── .gitkeep
├── logs/
│   └── .gitkeep
├── scripts/
│   ├── bootstrap.py
│   ├── download_shopbang_extension.py
│   ├── login_shopbang.py
│   ├── run_scheduler.py
│   └── run_streamlit.py
├── src/
│   └── ozon_selection/
│       ├── __init__.py
│       ├── main.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── clients/
│       │   │   ├── __init__.py
│       │   │   ├── anthropic_client.py
│       │   │   ├── api1688_client.py
│       │   │   ├── ozon_seller_client.py
│       │   │   └── supabase_client.py
│       │   └── schemas/
│       │       ├── __init__.py
│       │       ├── product_schema.py
│       │       └── review_schema.py
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── ozon/
│       │       ├── __init__.py
│       │       ├── category_collector.py
│       │       ├── keyword_collector.py
│       │       ├── product_collector.py
│       │       └── shopbang_auth.py
│       ├── models/
│       │   ├── __init__.py
│       │   ├── enums.py
│       │   ├── product.py
│       │   └── task_log.py
│       ├── panel/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   ├── components/
│       │   │   ├── __init__.py
│       │   │   ├── filters.py
│       │   │   └── metrics.py
│       │   └── pages/
│       │       ├── 1_dashboard.py
│       │       ├── 2_candidate_review.py
│       │       └── 3_task_logs.py
│       ├── repositories/
│       │   ├── __init__.py
│       │   ├── candidate_repository.py
│       │   ├── review_repository.py
│       │   └── task_log_repository.py
│       ├── scheduler/
│       │   ├── __init__.py
│       │   ├── jobs.py
│       │   └── scheduler.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── ai_selector.py
│       │   ├── data_enricher.py
│       │   ├── pricing_engine.py
│       │   └── review_service.py
│       ├── tasks/
│       │   ├── __init__.py
│       │   ├── collect_ozon_products.py
│       │   ├── push_review_queue.py
│       │   ├── run_ai_analysis.py
│       │   └── sync_1688_products.py
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── logger.py
│       │   ├── time.py
│       │   └── validators.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_ai_selector.py
    ├── test_ozon_product_collector.py
    ├── test_shopbang_auth.py
    └── test_settings.py
```

## 模块职责说明

- `config/`: 统一管理环境变量与系统配置，避免业务代码散落读取 `os.getenv`
- `src/ozon_selection/collectors/`: 负责 Ozon 页面采集与反爬兼容处理
- `src/ozon_selection/collectors/ozon/shopbang_auth.py`: 负责上品帮插件下载、解包、登录态保存与浏览器上下文复用
- `src/ozon_selection/collectors/ozon/product_collector.py`: 负责关键词搜索、滚动抓取、插件数据提取、红线评分、图片下载与 Excel 导出
- `src/ozon_selection/api/clients/`: 负责对外部服务的 HTTP 封装，包括 OpenAI、Supabase 等
- `src/ozon_selection/services/`: 负责业务编排，例如 Ozon 候选生成、1688 图搜图、参数比对
- `src/ozon_selection/repositories/`: 负责数据库读写，隔离数据访问逻辑
- `src/ozon_selection/tasks/`: 负责单一任务入口，供调度器调用
- `src/ozon_selection/scheduler/`: 负责定时任务注册与调度
- `src/ozon_selection/panel/`: 负责人工审核面板与可视化页面
- `tests/`: 负责配置、服务、任务层的单元测试与集成测试

## 核心业务流程

1. 执行 `python scripts/login_shopbang.py`，保存上品帮登录态和浏览器 profile。
2. 可选执行 `python scripts/collect_ozon_candidates_from_shopbang_hot.py`，从上品帮热销页提取关键词并直接驱动 Ozon 抓取。
3. 或执行 `python scripts/collect_ozon_candidates.py`，按关键词列表顺序抓取 Ozon 商品。
4. 对 Ozon 商品执行红线规则筛选、评分、主图下载和属性补抓。
5. 第 2/3 步会把本次关键词抓取结果自动写入 `SQLite`，形成一个待人工去重的批次。
6. 执行 `streamlit run src/ozon_selection/panel/app.py`，自动加载当前待处理批次做人审去重。
7. 人工勾选需要保留的唯一主图后提交，未勾选的商品会直接从 `SQLite` 删除；当前批次完成后页面会自动切换到下一个批次。
8. 执行 `python scripts/login_1688.py`，保存 1688 浏览器登录态。
9. 执行 `python scripts/search_1688_by_saved_images.py`，读取 SQLite 中人工审核完成后的 Ozon 主图，在 1688 做图搜图。
10. 先做 GPT 主图对比，每个 Ozon 商品只保留主图相似度最高的一条 1688 结果。
11. 对这条主图分最高的 1688 结果抓取详情页参数，包括属性、单价、重量和详情链接。
12. 1688 图搜图结果默认写入 `SQLite`，再通过 Web 页面导出 `XLSX`。

## 安装步骤

### 1. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 Playwright 浏览器

```bash
playwright install chromium
```

### 4. 配置环境变量

```bash
cp .env.example .env
```

然后根据你的实际环境填写以下关键配置：

- `OPENAI_API_KEY`
- `OPENAI_PRODUCT_PARSE_MODEL`
- `SQLITE_PATH`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SHOPBANG_USERNAME`
- `SHOPBANG_PASSWORD`
- `OZON_SCRAPE_KEYWORD`
- `OZON_SCRAPE_TARGET_PRODUCTS`
- `OZON_SCRAPE_DOWNLOAD_IMAGES`
- `ALIBABA1688_MAX_RESULTS`
- `ALIBABA1688_IMAGE_COMPARE_PASS_SCORE`

### 5. 下载并解包上品帮插件

```bash
python scripts/download_shopbang_extension.py
```

### 6. 登录上品帮并保存会话

```bash
python scripts/login_shopbang.py
```

如果已在 `.env` 中配置 `SHOPBANG_USERNAME` 和 `SHOPBANG_PASSWORD`，脚本会先检查当前 token 是否有效；若无效则自动登录，并自动写回 `auth-state.json`。

如果未配置账号密码，脚本会打开浏览器等待你手动登录。登录完成后回到终端按 Enter，系统会同时保留持久化浏览器目录和 `auth-state.json`。

## 运行方式

推荐按以下顺序执行：

```bash
python scripts/login_shopbang.py
python scripts/collect_ozon_candidates.py
streamlit run src/ozon_selection/panel/app.py
python scripts/login_1688.py
python scripts/search_1688_by_saved_images.py
```

如果你希望关键词不是来自 `.env`，而是来自上品帮热销页，可改为：

```bash
python scripts/login_shopbang.py
python scripts/collect_ozon_candidates_from_shopbang_hot.py
streamlit run src/ozon_selection/panel/app.py
python scripts/login_1688.py
python scripts/search_1688_by_saved_images.py
```

### 第 1 步：登录上品帮并保存状态

```bash
python scripts/login_shopbang.py
```

### 第 2 步：采集 Ozon 候选商品

```bash
python scripts/collect_ozon_candidates.py
```

该步骤会：

- 先检查上品帮登录状态，必要时自动登录或等待手动登录
- 按 `.env` 中的 `OZON_SCRAPE_KEYWORD` 列表顺序逐个抓取商品
- 也支持通过 `--keywords` 手动传入关键词列表
- 单个关键词抓取失败时只记录 `failed_keyword` 和 `failed_error`，不会中断后续关键词
- 跨 Ozon 搜索分页累计抓取商品，直到达到目标数量或没有新页
- 对所有商品执行红线规则筛选和评分
- 对通过筛选的商品补抓详情属性
- 将商品主图保存到 `OZON_SCRAPE_IMAGE_DIR`
- 自动把本次通过商品作为一个关键词批次写入 SQLite
- 在 Supabase 已配置时写入 `product_candidates`

手动传关键词示例：

```bash
python scripts/collect_ozon_candidates.py --keywords "关键词A,关键词B"
```

`OZON_SCRAPE_KEYWORD` 现在支持单个关键词，也支持列表，分隔符可用英文逗号、中文逗号、分号或换行，例如：

```env
OZON_SCRAPE_KEYWORD=关键词A,关键词B,关键词C
```

或：

```env
OZON_SCRAPE_KEYWORD=关键词A
关键词B
关键词C
```

Ozon 主图默认保存在：

- `data/raw/product_images/`

对应的绝对路径示例：

- `/Users/breaking/PyCharmMiscProject/select_product/data/raw/product_images`

保存结构通常按 Ozon SKU 分目录，例如：

```text
data/raw/product_images/
└── 1300617860/
    └── 1.jpg
```

第 3 步的 1688 图搜图会直接读取这里的本地主图。

多关键词采集会自动写入断点文件：

- `data/processed/ozon_keyword_checkpoint.json`

规则如下：

- 只有成功完成的关键词会写入 checkpoint
- 重新执行 `python scripts/collect_ozon_candidates.py` 时，已完成关键词会自动跳过
- 上次失败的关键词不会写入 checkpoint，重跑时会再次尝试
- 如需从头重跑全部关键词，删除这个 checkpoint 文件即可

### 第 2.1 步：从上品帮热销页自动提取关键词再采集 Ozon

```bash
python scripts/collect_ozon_candidates_from_shopbang_hot.py
```

该脚本会：

- 先复用 `login_shopbang.py` 保存的上品帮登录态
- 打开 `SHOPBANG_REMAI_URL`，默认是 `https://shopbang.cn/erp/#/remai`
- 在热销页尽量排除 `服装`、`电子产品`、`食品`、`药品`
- 点击查询，进入热销商品详情
- 从商品详情页提取上一级和上两级面包屑/类目关键词
- 对关键词去重后，直接调用 Ozon 采集主流程写入 SQLite

只提取关键词、不执行 Ozon 抓取时：

```bash
python scripts/collect_ozon_candidates_from_shopbang_hot.py --extract-only --max-products 10 --max-keywords 20
```

### 第 2.5 步：人工去重 Ozon 主图

先初始化 SQLite schema：

```bash
python scripts/import_ozon_candidates_to_sqlite.py --init-schema
```

如果你已经配置了 `SQLITE_PATH`，后续每次执行 `python scripts/collect_ozon_candidates.py` 都会自动把最新 Ozon 批次写入 SQLite，一般不需要再手动导入。

若需补导旧 manifest，可执行：

```bash
python scripts/import_ozon_candidates_to_sqlite.py --manifest data/exports/ozon_candidates_xxx.json
```

然后启动管理页：

```bash
streamlit run src/ozon_selection/panel/app.py
```

管理页规则如下：

- 每次 Ozon 关键词抓取会形成一个批次
- 页面会自动加载当前待处理批次
- 页面支持按关键词过滤删除匹配批次
- 页面会展示该批次全部 Ozon 主图，人工勾选需要保留的不重复商品
- 点击提交后，未勾选商品会直接从 SQLite 删除
- 当前批次完成后会自动跳到下一个批次
- 去重完成的关键词批次会显示“已完成”状态

### 第 3 步：登录 1688 并用人工审核后的主图搜图

首次执行前可先单独登录：

```bash
python scripts/login_1688.py
```

然后执行图搜图：

```bash
python scripts/search_1688_by_saved_images.py
```

该步骤会：

- 读取 SQLite 中已人工去重完成的 Ozon 商品主图
- 检查 1688 登录状态，必要时跳转登录页等待手动登录
- 上传本地主图到 1688 做图搜图
- 提取第一页结果
- 先执行 GPT 主图对比
- 每个 Ozon 商品只保留主图相似度最高的一条结果
- 只对这条结果抓取 1688 详情页参数，包括属性、单价、重量和详情链接
- 将结果写入 SQLite
- 在 Supabase 已配置时写入 `supplier_links`

调试时建议先跑小样本：

```bash
python scripts/search_1688_by_saved_images.py --max-products 5 --max-results 5
```

### 第 4 步内部细流程

`search_1688_by_saved_images.py` 内部按以下顺序处理：

1. 读取 SQLite 中人工去重完成后保留的 Ozon 商品
2. 过滤出存在本地主图的 Ozon 商品
3. 上传图片到 1688，抓取第一页搜索结果
4. 对每个 1688 结果执行 GPT 主图对比
5. 每个 Ozon 商品只保留主图相似度最高的一条结果
6. 打开这条结果的 1688 详情页，抓取属性、单价、重量等参数
7. 写入 SQLite，并在已配置时落库

### 启动 Streamlit 审核面板

```bash
streamlit run src/ozon_selection/panel/app.py
```

### 启动 APScheduler 调度器

```bash
python scripts/run_scheduler.py
```

### 单独执行 Ozon 采集任务

```bash
python -c "import sys; sys.path.insert(0, 'src'); from ozon_selection.tasks.collect_ozon_products import run_collect_ozon_products; run_collect_ozon_products()"
```

采集任务启动前会先检查上品帮 token 是否有效；若已配置账号密码且 token 失效，会自动重新登录。

默认会按 `.env` 中的 `OZON_SCRAPE_KEYWORD`、`OZON_SCRAPE_TARGET_PRODUCTS`、`OZON_SCRAPE_SORTING` 等参数执行，并把结果导出到 `OZON_SCRAPE_OUTPUT_DIR`，商品图片保存到 `OZON_SCRAPE_IMAGE_DIR`。

命令行输出会额外打印：

- `success_count`
- `failure_count`
- `skipped_count`
- `checkpoint_path`
- `skipped_keywords`

### 运行项目健康检查

```bash
python scripts/bootstrap.py
```

## 推荐的 Supabase 表设计

建议至少包含以下数据表：

- `product_candidates`: 候选商品主表
- `product_analysis_results`: AI 分析结果表
- `product_reviews`: 人工审核记录表
- `task_logs`: 任务执行日志表
- `supplier_links`: 1688 货源映射表

## 后续扩展建议

- 在 `product_collector.py` 中补充商品卡片、销量、价格、评论数等结构化解析逻辑
- 增加代理池与指纹浏览器配置，提升采集稳定性
- 增加 Ozon 搜索词趋势与类目增长率分析
- 增加候选商品利润测算模型，纳入运费、平台佣金、退款率
- 增加 AI 提示词版本管理与结果打分追踪
- 增加人工审核结果回流，构建选品反馈闭环
