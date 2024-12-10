from __future__ import annotations

import unittest
from unittest.mock import patch

from notion_backup.notion_client import NotionClient, _RateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_rejects_non_positive_rate(self) -> None:
        with self.assertRaises(ValueError):
            _RateLimiter(0, burst=3)
        with self.assertRaises(ValueError):
            _RateLimiter(-1.5, burst=3)

    def test_rejects_non_positive_burst(self) -> None:
        with self.assertRaises(ValueError):
            _RateLimiter(2.0, burst=0)
        with self.assertRaises(ValueError):
            _RateLimiter(2.0, burst=-1)

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_burst_capacity_allows_that_many_calls_with_no_wait(self, monotonic_mock, sleep_mock) -> None:
        monotonic_mock.return_value = 100.0
        limiter = _RateLimiter(requests_per_second=2.0, burst=3)

        results = [limiter.wait(), limiter.wait(), limiter.wait()]

        self.assertEqual(results, [0.0, 0.0, 0.0])
        sleep_mock.assert_not_called()

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_call_beyond_burst_waits_for_one_token_to_refill(self, monotonic_mock, sleep_mock) -> None:
        monotonic_mock.side_effect = [
            100.0,  
            100.0,  
            100.0,  
            100.0,  
            100.5,  
        ]
        limiter = _RateLimiter(requests_per_second=2.0, burst=2)

        first = limiter.wait()
        second = limiter.wait()
        third = limiter.wait()

        self.assertEqual([first, second], [0.0, 0.0])
        self.assertEqual(third, 0.5)
        sleep_mock.assert_called_once_with(0.5)

    @patch("notion_backup.notion_client.time.sleep")
    @patch("notion_backup.notion_client.time.monotonic")
    def test_waiting_the_full_interval_between_bursts_avoids_extra_delay(self, monotonic_mock, sleep_mock) -> None:
        monotonic_mock.side_effect = [
            100.0, 
            100.0,  
            100.0,  
            100.0,  
            102.0, 
            102.0, 
            102.0,  
            102.0,  
        ]
        limiter = _RateLimiter(requests_per_second=2.0, burst=3)

        first_burst = [limiter.wait(), limiter.wait(), limiter.wait()]
        second_burst = [limiter.wait(), limiter.wait(), limiter.wait()]

        self.assertEqual(first_burst, [0.0, 0.0, 0.0])
        self.assertEqual(second_burst, [0.0, 0.0, 0.0])
        sleep_mock.assert_not_called()


class NotionClientDefaultsTests(unittest.TestCase):
    def test_defaults_favor_full_page_size_and_measured_pacing(self) -> None:
        client = NotionClient(token="fake-token")

        self.assertEqual(client.page_size, 100)
        self.assertEqual(client.requests_per_second, 3.0)
        self.assertEqual(client.burst, 8)

    def test_page_size_is_still_clamped_to_the_api_maximum(self) -> None:
        client = NotionClient(token="fake-token", page_size=500)

        self.assertEqual(client.page_size, 100)

    def test_requests_per_second_and_burst_are_configurable(self) -> None:
        client = NotionClient(token="fake-token", requests_per_second=1.0, burst=5)

        self.assertEqual(client._rate_limiter._rate, 1.0)
        self.assertEqual(client._rate_limiter._capacity, 5.0)

    def test_starts_with_zero_running_totals(self) -> None:
        client = NotionClient(token="fake-token")

        self.assertEqual(client.total_requests, 0)
        self.assertEqual(client.total_rate_limit_wait_seconds, 0.0)
        self.assertEqual(client.total_retry_wait_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
