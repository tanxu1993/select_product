# 标准执行命令清单

本文档按当前代码实现，整理 4 条实际选品路径、对应脚本、前置条件、产物和限制。

## 先说结论

当前代码里这 4 条路径对应关系如下：

1. 根据商品找热门店铺，再去店铺里找商品
   - 已实现
   - 脚本：`find_ozon_reviewed_sellers.py` + `classify_ozon_reviewed_seller_shops.py` + `collect_ozon_candidates_from_reviewed_seller_shops.py`
2. 根据类目找商品
   - 部分实现
   - 当前没有“自动从 Ozon 首页点类目并遍历”的独立脚本
   - 当前可用入口：先人工把类目页准备好，再由 `collect_ozon_candidates_from_manual_page.py` 接管
   - `src/ozon_selection/collectors/ozon/category_collector.py` 目前仍是占位实现
3. 根据热卖关键词找商品
   - 已实现
   - 脚本：`collect_ozon_candidates_from_shopbang_hot.py` + `collect_ozon_candidates.py`
4. 根据 Shopbang 历史关键词找商品
   - 已实现
   - 脚本：`collect_shopbang_history_keywords.py` + `collect_ozon_candidates_from_shopbang_history_keywords.py`

## 共用前置步骤

### 1. 启动隔离 Chrome

```bash
python scripts/start_shopbang_cdp_chrome.py
```

作用：

- 启动独立 Chrome
- 开启本地 CDP 调试端口
- 供 Ozon / 上品帮脚本复用同一个浏览器上下文

常用参数：

- `--port`
- `--browser-path`
- `--user-data-dir`
- `--url`

标准示例：

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/start_shopbang_cdp_chrome.py --port 9333
```

环境变量：

```env
SHOPBANG_CDP_URL=http://127.0.0.1:9222
```

### 2. 登录上品帮

```bash
python scripts/login_shopbang.py
```

说明：

- 保存 `auth-state.json`
- 保存浏览器 profile
- 后续 Ozon / 上品帮脚本都会复用这套登录态

## 路径一：根据商品找热门店铺，再去店铺里找商品

### 代码现状

这条路径已经完整落地，分 3 步。

### 第 1 步：根据商品找有评论的跟卖店铺

脚本：

```bash
python scripts/find_ozon_reviewed_sellers.py
```

实际行为：

1. 打开默认专题页 `https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/`
2. 读取当前列表页商品 URL
3. 逐个打开商品详情页
4. 寻找跟卖入口
5. 提取“有评论”的跟卖店铺
6. 写入 SQLite
7. 导出 `xlsx` 和 `json`

去重规则：

- 已处理过的商品 SKU 会从 SQLite 中读取并跳过
- 同一轮里重复商品 SKU 也会跳过
- 同一个 `seller_url` 写店铺表时不会重复插入

输出产物：

- `data/exports/ozon_reviewed_sellers_*.xlsx`
- `data/exports/ozon_reviewed_sellers_*.json`
- SQLite 表：
  - `ozon_reviewed_seller_products`
  - `ozon_reviewed_seller_shops`

可用参数：

- `--start-url`
  - 指定起始专题页或列表页
- `--max-products`
  - 最多处理多少个商品详情页
- `--max-scroll-rounds`
  - 跟卖面板最多滚动多少轮
- `--background`

标准示例：

```bash
python scripts/find_ozon_reviewed_sellers.py --max-products 20
python scripts/find_ozon_reviewed_sellers.py --max-products 100 --background
python scripts/find_ozon_reviewed_sellers.py --start-url "https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/?currency_price=500.000%3B8000.000&opened=category" --max-products 50
```

### 第 2 步：判断店铺类型

脚本：

```bash
python scripts/classify_ozon_reviewed_seller_shops.py
```

实际行为：

1. 从 SQLite 的 `ozon_reviewed_seller_shops` 读取店铺
2. 打开店铺页并抽样商品
3. 综合一级类目、品牌分布、标题分散度判断店铺类型
4. 将店铺写回 `杂货铺` 或 `垂直店`

当前规则：

- 默认只处理还没有 `shop_type` 的店铺
- `--recheck-all` 可重跑全部店铺，刷新已有分类
- 当前“按店铺抓商品”脚本只会遍历 `shop_type='杂货铺'` 的店铺

输出产物：

- SQLite 表：
  - `ozon_reviewed_seller_shops`

可用参数：

- `--max-shops`
  - 最多处理多少家店铺
  - `0` 表示处理全部
- `--recheck-all`
  - 忽略已有分类，全部重跑
- `--sample-target`
  - 每家店铺最多抽样多少个商品用于分类
- `--background`

标准示例：

```bash
python scripts/classify_ozon_reviewed_seller_shops.py
python scripts/classify_ozon_reviewed_seller_shops.py --max-shops 50
python scripts/classify_ozon_reviewed_seller_shops.py --recheck-all --background
```

### 第 3 步：根据店铺找商品并按规则筛选

脚本：

```bash
python scripts/collect_ozon_candidates_from_reviewed_seller_shops.py
```

实际行为：

1. 直接从 SQLite 的 `ozon_reviewed_seller_shops` 读取店铺
2. 跳过已标记 `completed` 的店铺
3. 只处理 `shop_type='杂货铺'` 的店铺
4. 逐个打开店铺页
5. 抓取店铺里的商品
6. 按既有选品规则筛选
7. 导出结果并写入 SQLite
8. 把店铺标记为 `completed` / `failed`

当前去重和状态规则：

- 已完成店铺下次不会重复爬
- 店铺抓取开始会写 `crawl_status='in_progress'`
- 成功后写 `crawl_status='completed'`
- 失败后写 `crawl_status='failed'`

输出产物：

- `data/exports/选品_seller_*.xlsx`
- SQLite 表：
  - `ozon_reviewed_seller_shops`
  - `ozon_keyword_batches`
  - `ozon_batch_products`

可用参数：

- `--max-shops`
  - 最多处理多少家未完成店铺
- `--max-products-per-shop`
  - 单店最多抓多少商品
  - `0` 表示不限制，直到抓完整个店铺
- `--background`

标准示例：

```bash
python scripts/collect_ozon_candidates_from_reviewed_seller_shops.py
python scripts/collect_ozon_candidates_from_reviewed_seller_shops.py --max-shops 20
python scripts/collect_ozon_candidates_from_reviewed_seller_shops.py --max-shops 5 --max-products-per-shop 50
```

### 路径一标准执行顺序

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/find_ozon_reviewed_sellers.py --max-products 100
python scripts/classify_ozon_reviewed_seller_shops.py
python scripts/collect_ozon_candidates_from_reviewed_seller_shops.py
```

## 路径二：根据类目找商品

### 代码现状

这条路径当前没有“全自动专用脚本”。

现状是：

- `src/ozon_selection/collectors/ozon/category_collector.py` 还是占位类，`collect()` 直接返回空列表
- 当前可用的生产入口是 `scripts/collect_ozon_candidates_from_manual_page.py`
- 也就是：人工先把 Ozon 类目页准备好，再由脚本接管采集

### 当前可用执行方式

脚本：

```bash
python scripts/collect_ozon_candidates_from_manual_page.py
```

实际行为：

1. 打开一个 Ozon 标签页
2. 等待人工手动输入目标 URL
3. 等待人工把价格设为 `500-8000`
4. 自动识别当前准备好的 Ozon 列表页
5. 复用和关键词采集相同的筛选规则
6. 导出结果并写入 SQLite

适用场景：

- 手动打开某个类目页
- 手动打开某个专题页
- 手动打开任意 Ozon 列表页

可用参数：

- `--start-url`
- `--keyword`
  - 指定导出和入库标签
- `--background`
  - 不建议使用，因为这个脚本依赖人工操作

标准示例：

```bash
python scripts/collect_ozon_candidates_from_manual_page.py
python scripts/collect_ozon_candidates_from_manual_page.py --start-url https://www.ozon.ru/
python scripts/collect_ozon_candidates_from_manual_page.py --keyword "category_umnyy_dom_20260514"
```

### 当前标准操作方式

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/collect_ozon_candidates_from_manual_page.py
```

然后在浏览器中手动执行：

1. 打开 Ozon 首页
2. 选择一个大类
3. 进入目标类目列表页
4. 把价格设置为 `500-8000`
5. 回终端按 Enter，让脚本接管

## 路径三：根据热卖关键词找商品

### 代码现状

这条路径已经落地，分 3 步：

1. 从 Shopbang 热卖页提词
2. 用关键词跑 Ozon 商品筛选
3. 在网页面板里做人审去重，得到最终主图

### 第 1 步：从 Shopbang 热卖页提取关键词

脚本：

```bash
python scripts/collect_ozon_candidates_from_shopbang_hot.py
```

实际行为：

1. 打开 Shopbang 热卖页
2. 等待人工选择 1 个一级类目并点击“查询”
3. 遍历当前已选类目的热卖结果页
4. 打开热卖商品对应的 Ozon 商品链接
5. 提取上一级、上两级关键词
6. 去重并写入 SQLite 关键词池
7. 保存类目翻页进度，供下次续跑

SQLite 相关表：

- `ozon_keyword_pool`
- `shopbang_hot_category_progress`

可用参数：

- `--max-pages`
- `--max-products`
  - 兼容旧参数，当前等同 `--max-pages`
- `--extract-only`
- `--run-ozon-after-save`

标准示例：

```bash
python scripts/collect_ozon_candidates_from_shopbang_hot.py --max-pages 2
python scripts/collect_ozon_candidates_from_shopbang_hot.py --max-pages 2 --extract-only
python scripts/collect_ozon_candidates_from_shopbang_hot.py --max-pages 2 --run-ozon-after-save
```

说明：

- 该脚本要求人工在页面中选择类目并点击“查询”
- 当前进度记录粒度是“类目翻页进度”
- 不是“单个热卖商品 URL 的处理进度”

## 路径四：根据 Shopbang 历史关键词找商品

### 代码现状

这条路径已经落地，分 2 步：

1. 从 Shopbang 历史页提取 500-20000 价格区间关键词
2. 从 `shopbang_history_keywords` 读取未爬关键词，执行 Ozon 商品筛选，并回写关键词爬取状态

### 第 1 步：从 Shopbang 历史页提取 500-20000 价格区间关键词

脚本：

```bash
python scripts/collect_shopbang_history_keywords.py
```

实际行为：

1. 打开 `https://shopbang.cn/erp/#/history`
2. 目标筛选条件是“商品平均价格 > 500”且“商品平均价格 < 20000”
3. 优先尝试自动设置筛选条件
4. 如果自动设置未拿到稳定请求体，则回退为人工确认后继续
5. 捕获历史页接口请求体
6. 直接按接口翻页抓取最多 100 页关键词
7. 过滤掉命中 `衣服`、`鞋子`、`药品`、`手机` 的关键词
8. 写入 SQLite 新表并按关键词去重
9. 同时导出 `json` 和 `xlsx`

输出产物：

- `data/exports/shopbang_history_keywords_*.json`
- `data/exports/shopbang_history_keywords_*.xlsx`
- SQLite 表：
  - `shopbang_history_keywords`

可用参数：

- `--min-avg-price`
  - 商品平均价格下限，默认 `500`
- `--max-avg-price`
  - 商品平均价格上限，默认 `20000`
- `--max-pages`
  - 最多抓取多少页，默认 `100`
- `--exclude-keywords`
  - 要排除的关键词片段，默认 `衣服,鞋子,药品,手机`
- `--background`
  - 后台模式运行浏览器
  - 如果登录态失效或自动筛选失败，后台模式无法人工介入

标准示例：

```bash
python scripts/collect_shopbang_history_keywords.py
python scripts/collect_shopbang_history_keywords.py --max-pages 100
python scripts/collect_shopbang_history_keywords.py --exclude-keywords "衣服,鞋子,药品,手机"
```

补充说明：

- 该脚本只负责“提词入库”
- 关键词表会保留爬取状态字段，供路径四第 2 步消费
- 当前入库前会额外过滤服饰、鞋靴、内衣、礼服等非目标关键词

### 第 2 步：从历史关键词表读取未爬关键词并筛选商品

脚本：

```bash
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py
```

实际行为：

1. 从 SQLite 表 `shopbang_history_keywords` 随机读取未爬取关键词
2. 用关键词搜索 Ozon
3. 抓取列表页商品
4. 按既有红线和评分规则筛选
5. 补抓属性、配送、退货等信息
6. 导出 `xlsx`
7. 写入 SQLite 批次表
8. 将该关键词标记为已爬取
9. 写回最近执行状态、最近错误、最近爬取时间和累计爬取次数

输出产物：

- `data/exports/选品_*.xlsx`
- SQLite 表：
  - `shopbang_history_keywords`
  - `ozon_keyword_batches`
  - `ozon_batch_products`

可用参数：

- `--target-products`
  - 单个关键词目标抓取商品数
  - 大于 `0` 时会覆盖 `.env` 中的 `OZON_SCRAPE_TARGET_PRODUCTS`
- `--take-count`
  - 本次最多实际执行多少个历史关键词
  - 大于 `0` 时会覆盖 `--pool-count`
- `--pool-count`
  - 表示从 `shopbang_history_keywords` 中最多读取多少个未爬关键词执行
- `--background`
  - 后台模式运行浏览器
  - 适合已有稳定登录态、希望静默执行时使用

状态规则：

- `used=0` 表示未爬取
- `used=1` 表示至少已爬过 1 次
- 每次执行后会更新：
  - `used_at`
  - `last_used_status`
  - `last_error`
  - `use_count`

标准示例：

```bash
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py --target-products 1 --take-count 1
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py --take-count 1
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py --take-count 5
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py --pool-count 10
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py --take-count 5 --background
```

### 第 3 步：在网页里去重，得到最终主图

当前不是单独 Python 业务脚本，而是 Streamlit 面板。

启动命令：

```bash
streamlit run src/ozon_selection/panel/app.py
```

说明：

- 面板会读取当前待处理的 SQLite 批次
- 人工勾选保留的唯一商品主图
- 未勾选项会从批次里删除
- 去重完成后，后续 1688 图搜图会读取这些保留商品

辅助脚本：

```bash
python scripts/run_streamlit.py
```

这个脚本只打印推荐启动命令，不直接启动服务。

### 路径三标准执行顺序

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/collect_ozon_candidates_from_shopbang_hot.py --max-pages 2
python scripts/collect_ozon_candidates.py --take-count 5
streamlit run src/ozon_selection/panel/app.py
```

如果希望提词后立即跑 Ozon，可直接：

```bash
python scripts/collect_ozon_candidates_from_shopbang_hot.py --max-pages 2 --run-ozon-after-save
```

### 路径四标准执行顺序

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/collect_shopbang_history_keywords.py --max-pages 100
python scripts/collect_ozon_candidates_from_shopbang_history_keywords.py --take-count 5
streamlit run src/ozon_selection/panel/app.py
```

## 共用后续：1688 图搜图

当前四条路径里，只要商品已经进入 SQLite 并完成人审去重，后续都可以进入 1688 图搜图。

脚本：

```bash
python scripts/login_1688.py
python scripts/search_1688_by_saved_images.py --max-products 5 --max-results 5
```

作用：

- 读取 SQLite 中已完成人审去重的 Ozon 商品主图
- 上传到 1688 图搜图
- 先做 GPT 主图相似度筛选
- 再抓最优结果的详情参数

## 当前推荐工作流

### 路径一

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/find_ozon_reviewed_sellers.py --max-products 100
python scripts/classify_ozon_reviewed_seller_shops.py
python scripts/collect_ozon_candidates_from_reviewed_seller_shops.py
streamlit run src/ozon_selection/panel/app.py
```

### 路径二

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/collect_ozon_candidates_from_manual_page.py
streamlit run src/ozon_selection/panel/app.py
```

### 路径三

```bash
python scripts/start_shopbang_cdp_chrome.py
python scripts/login_shopbang.py
python scripts/collect_ozon_candidates_from_shopbang_hot.py --max-pages 2
python scripts/collect_ozon_candidates.py --take-count 5
streamlit run src/ozon_selection/panel/app.py
```

## 当前限制

- “根据类目找商品”的自动类目采集器还没实现；现在只能通过人工准备类目页后接管
- “根据商品找店铺，再按店铺抓商品”已经支持先做店铺类型判断，并在抓店铺阶段跳过非 `杂货铺` 与已完成店铺，但大店铺在全量抓取时终端进度可观测性还偏弱
