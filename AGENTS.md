# Repository Guidelines

## Start Here
Before changing code, read this file first, then inspect the latest artifacts in `data/exports/`. Prefer the newest `ozon_candidates_*.json`, `ozon_evaluated_*.json`, and `alibaba1688_image_search_*.json` to recover prior run context. If the task mentions browser login or scraping failures, also check `auth-state*.json`, `browser-profile*`, and the matching script in `scripts/`.

## Project Structure & Current Workflow
Core code lives in `src/ozon_selection/`. Use `collectors/` for Playwright scraping, `services/` for orchestration, `repositories/` for Supabase writes, and `scripts/` as the runnable entrypoints. The active business flow is:
1. `python scripts/login_shopbang.py`
2. `python scripts/collect_ozon_candidates.py`
3. `python scripts/search_1688_by_saved_images.py`

Step 2 now paginates across Ozon search pages, saves passing and rejected products, exports Excel + JSON, downloads Ozon main images, and stores Ozon attributes. Step 3 uploads saved Ozon images to 1688, clicks the `搜索图片` button, collects first-page results, runs GPT image prefilter first, opens detail pages only for passed items, then compares Ozon and 1688 attributes. The mapping is `1 Ozon -> many 1688`.

## Build, Test, and Debug Commands
Install with `pip install -r requirements.txt` and `playwright install chromium`. Use:
- `python scripts/login_1688.py`: open 1688 and save login state.
- `python scripts/collect_ozon_candidates.py`: collect Ozon candidates.
- `python scripts/search_1688_by_saved_images.py --max-products 5 --max-results 5`: safe debug run.
- `pytest -q`: run tests.

## Key Config
Main settings are in `.env` and `config/settings.py`. The most important keys are:
- `OZON_SCRAPE_KEYWORD`, `OZON_SCRAPE_TARGET_PRODUCTS`
- `ALIBABA1688_MAX_RESULTS`, `ALIBABA1688_IMAGE_COMPARE_PASS_SCORE`
- `OPENAI_API_KEY`, `OPENAI_PRODUCT_PARSE_MODEL`
- `DEFAULT_EXCHANGE_RATE_CNY_TO_RUB`

Shipping logic uses CNY cost converted to RUB via `DEFAULT_EXCHANGE_RATE_CNY_TO_RUB`. A target of `OZON_SCRAPE_TARGET_PRODUCTS=1000` means browse up to 1000 Ozon items, not “only save 1000 passed items”.

## Naming, Data, and Safety Rules
Use 4-space indentation, type hints, and `snake_case`. Current GPT result fields use `same_product`, `ai_same_product`, and `ai_image_same_product`; keep backward-compatible parsing if touching model output. Do not revert unrelated files. If Supabase is still placeholder-configured, expect DB writes to be skipped while Excel/JSON exports continue to work.
