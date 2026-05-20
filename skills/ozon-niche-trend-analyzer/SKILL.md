---
name: ozon-niche-trend-analyzer
description: Analyze Ozon niche opportunities from a broad category and return 5-8 high-potential subcategory directions for continued research. Use when the user provides or implies an Ozon big category and wants narrowed niche ideas, Chinese and Russian search keywords, weighted scoring, reasons, and risk prompts rather than final procurement conclusions. Use for exploratory trend analysis, category decomposition, shortlist generation, and next-step research planning. Do not use for 1688 supply validation, final go/no-go decisions, or product link collection.
---

# Ozon Niche Trend Analyzer

## Overview

Turn one Ozon big category into a ranked shortlist of 5-8 subcategory directions for further research. Keep the result exploratory, evidence-led, and clearly non-final.

## Workflow

1. Confirm the platform is Ozon and treat the user input as a broad category, not as a final niche.
2. Expand the big category into 12-20 candidate sub-directions before scoring.
3. Eliminate obviously weak directions early:
   - heavy compliance or certification burden
   - strong brand or IP concentration
   - excessive seasonality unless the user explicitly wants seasonal plays
   - fragile, oversized, or return-prone directions with weak upside
4. Score the remaining candidates with the rubric in [references/scoring-rubric.md](references/scoring-rubric.md).
5. Keep only the top 5-8 directions.
6. Sort strictly by `总分` from high to low.
7. Present the result as a research shortlist, not a final conclusion.

## Research Mode

- Use current signals when the user asks for trends, recent opportunities, current hot categories, or similar time-sensitive judgments.
- If live research is available, prefer recent Ozon marketplace signals, search behavior clues, seasonality context, and current consumer demand indicators.
- If live research is not available, state that the output is a heuristic shortlist for continued research.
- Infer cautiously. Do not invent certainty.

## Output Rules

- Output exactly 5-8 directions.
- Include these fields for every direction:
  - `中文关键词`
  - `俄文关键词`
  - `总分`
  - `分项得分`
  - `潜力原因`
  - `风险提示`
- Keep `分项得分` aligned with the rubric dimensions and show numeric values.
- Do not perform 1688 supply validation.
- Do not output product links.
- Do not say the direction is “最终结论”, “必做”, or equivalent absolute wording.
- Use wording such as `优先调研`, `建议跟踪`, `可继续验证`.

## Output Shape

Use a compact table when possible. Follow it with 1-3 short notes on how to continue research.

Recommended columns:

| 排名 | 中文关键词 | 俄文关键词 | 总分 | 分项得分 | 潜力原因 | 风险提示 |
| --- | --- | --- | ---: | --- | --- | --- |

`分项得分` format:

`需求热度 22 / 竞争友好 16 / 客单与利润空间 18 / 稳定性 14 / 履约友好 9`

## Quality Bar

- Prefer specific sub-directions over vague labels.
- Make Chinese and Russian keywords usable as research seed queries.
- Keep `潜力原因` focused on demand, competition, pricing, repeatability, or category expansion logic.
- Keep `风险提示` concrete and action-oriented.
- If two directions are similar, keep the stronger one and diversify the shortlist.

## Closeout

End with a short statement that the shortlist is for continued research on Ozon and still needs deeper validation on demand, competition, compliance, and unit economics.
