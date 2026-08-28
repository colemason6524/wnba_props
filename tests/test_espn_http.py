from __future__ import annotations

import unittest
from unittest.mock import call, patch

from wnba_props.utils import (
    ESPN_API_FALLBACK_HEADERS,
    ESPN_API_HEADERS,
    fetch_espn_json,
)


class EspnHttpTests(unittest.TestCase):
    @patch("wnba_props.utils.fetch_json")
    def test_uses_api_safe_headers(self, mock_fetch_json) -> None:
        mock_fetch_json.return_value = {"events": []}

        result = fetch_espn_json("https://site.api.espn.com/example")

        self.assertEqual({"events": []}, result)
        mock_fetch_json.assert_called_once_with(
            "https://site.api.espn.com/example",
            headers=ESPN_API_HEADERS,
            timeout=30,
        )

    @patch("wnba_props.utils.fetch_json")
    def test_retries_a_403_with_fallback_headers(self, mock_fetch_json) -> None:
        mock_fetch_json.side_effect = [RuntimeError("HTTP 403 for ESPN"), {"events": []}]

        result = fetch_espn_json("https://site.api.espn.com/example")

        self.assertEqual({"events": []}, result)
        self.assertEqual(
            [
                call(
                    "https://site.api.espn.com/example",
                    headers=ESPN_API_HEADERS,
                    timeout=30,
                ),
                call(
                    "https://site.api.espn.com/example",
                    headers=ESPN_API_FALLBACK_HEADERS,
                    timeout=30,
                ),
            ],
            mock_fetch_json.call_args_list,
        )

    @patch("wnba_props.utils.fetch_json")
    def test_does_not_mask_non_403_failures(self, mock_fetch_json) -> None:
        mock_fetch_json.side_effect = RuntimeError("HTTP 500 for ESPN")

        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            fetch_espn_json("https://site.api.espn.com/example")

        self.assertEqual(1, mock_fetch_json.call_count)


if __name__ == "__main__":
    unittest.main()
