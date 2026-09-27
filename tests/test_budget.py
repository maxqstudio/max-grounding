from __future__ import annotations

import unittest

from max_grounding.budget import SearchBudget
from max_grounding.errors import SearchBudgetExceeded


class SearchBudgetTests(unittest.TestCase):
    def test_two_calls_are_allowed_and_third_is_rejected(self) -> None:
        budget = SearchBudget(max_calls=2)
        budget.consume_search_call()
        budget.consume_search_call()
        self.assertEqual(budget.calls_used, 2)
        self.assertEqual(budget.remaining, 0)
        with self.assertRaises(SearchBudgetExceeded):
            budget.consume_search_call()

    def test_budget_itself_rejects_more_than_product_cap(self) -> None:
        with self.assertRaises(ValueError):
            SearchBudget(max_calls=3)


if __name__ == "__main__":
    unittest.main()
