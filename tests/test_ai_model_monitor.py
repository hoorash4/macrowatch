from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import ai_model_monitor  # noqa: E402


class AiModelMonitorTests(unittest.TestCase):
    @patch.object(ai_model_monitor, "send_model_email")
    @patch.object(ai_model_monitor, "monitor_request")
    def test_no_new_model_does_not_send_email(self, request, send):
        request.return_value = {"model_ids": []}
        ai_model_monitor.run()
        send.assert_not_called()
        request.assert_called_once_with("scan")

    @patch.object(ai_model_monitor, "send_model_email")
    @patch.object(ai_model_monitor, "monitor_request")
    def test_success_is_recorded_after_email(self, request, send):
        scan = {"model_ids": ["gpt-7-nova"], "fomc": "gpt-6-sol", "standard": "gpt-6-luna"}
        request.return_value = scan
        ai_model_monitor.run()
        send.assert_called_once_with(["gpt-7-nova"], scan)
        self.assertEqual(request.call_args_list[-1].args, ("report",))
        self.assertEqual(request.call_args_list[-1].kwargs, {"model_ids": ["gpt-7-nova"], "success": True})

    @patch.object(ai_model_monitor, "send_model_email", side_effect=RuntimeError("smtp failed"))
    @patch.object(ai_model_monitor, "monitor_request")
    def test_email_failure_is_recorded_without_success(self, request, _send):
        request.return_value = {"model_ids": ["gpt-7-nova"], "fomc": "gpt-6-sol", "standard": "gpt-6-luna"}
        with self.assertRaisesRegex(RuntimeError, "smtp failed"):
            ai_model_monitor.run()
        self.assertEqual(request.call_args_list[-1].kwargs, {"model_ids": ["gpt-7-nova"], "success": False})


if __name__ == "__main__":
    unittest.main()
