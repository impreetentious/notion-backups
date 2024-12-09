from __future__ import annotations

import unittest
from unittest.mock import patch

from notion_backup.notion_client import NotionClient, _RateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_rejects_non_positive_rate(self) -> None:
        with self.assertRaises(ValueError):
            _RateLimiter(0)
        with self.assertRaises(ValueError):
            _RateLimiter(-1.5)

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_first_call_never_waits(self, monotonic_mock, sleep_mock) -> None:
        monotonic_mock.return_value = 100.0
        limiter = _RateLimiter(requests_per_second=2.0)

        limiter.wait()

        sleep_mock.assert_not_called()

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_back_to_back_calls_are_spaced_by_the_minimum_interval(self, monotonic_mock, sleep_mock) -> None:
        monotonic_mock.return_value = 100.0
        limiter = _RateLimiter(requests_per_second=2.0)  

        limiter.wait()
        limiter.wait()

        sleep_mock.assert_called_once_with(0.5)

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_call_after_interval_has_elapsed_does_not_wait(self, monotonic_mock, sleep_mock) -> None:
        limiter = _RateLimiter(requests_per_second=2.0) 

        monotonic_mock.return_value = 100.0
        limiter.wait()

        monotonic_mock.return_value = 100.6
        limiter.wait()

        sleep_mock.assert_not_called()

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_third_call_accounts_for_the_reserved_slot_of_the_second(self, monotonic_mock, sleep_mock) -> None:

        monotonic_mock.return_value = 100.0
        limiter = _RateLimiter(requests_per_second=2.0) 

        limiter.wait()  
        limiter.wait()  
        limiter.wait()  

        self.assertEqual(sleep_mock.call_args_list[-1].args[0], 1.0)


class NotionClientDefaultsTests(unittest.TestCase):
    def test_defaults_favor_full_page_size_and_conservative_pacing(self) -> None:
        client = NotionClient(token="fake-token")

        self.assertEqual(client.page_size, 100)
        self.assertEqual(client.requests_per_second, 2.5)

    def test_page_size_is_still_clamped_to_the_api_maximum(self) -> None:
        client = NotionClient(token="fake-token", page_size=500)

        self.assertEqual(client.page_size, 100)

    def test_requests_per_second_is_configurable(self) -> None:
        client = NotionClient(token="fake-token", requests_per_second=1.0)

        self.assertEqual(client._rate_limiter._min_interval, 1.0)


if __name__ == "__main__":
    unittest.main()
