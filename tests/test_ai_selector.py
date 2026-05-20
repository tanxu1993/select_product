"""AI 选品服务测试。"""

from ozon_selection.services.ai_selector import AISelectorService


def test_score_candidate_returns_todo_status() -> None:
    """确保 AI 选品服务当前返回占位结果。"""

    service = AISelectorService()
    result = service.score_candidate({"sku": "demo"})
    assert result["status"] == "todo"
