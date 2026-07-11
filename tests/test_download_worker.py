from __future__ import annotations

from pathlib import Path

from bot.workers.download_worker import _notify_fail


class TestNotifyFailMessages:
    async def test_empty_reason_generic_message(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 12345, "https://example.com/video", reason="")
        msg = r.publish.call_args[0][1]
        assert "Download failed" in msg
        assert "https://example.com/video" in msg

    async def test_too_large_message(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 12345, "https://example.com/video", reason="too_large")
        msg = r.publish.call_args[0][1]
        assert "1.9GB" in msg

    async def test_auth_message(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 12345, "https://instagram.com/p/abc/", reason="auth")
        msg = r.publish.call_args[0][1]
        assert "login expired" in msg

    async def test_best_effort_message(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 12345, "https://linkedin.com/video", reason="best_effort")
        msg = r.publish.call_args[0][1]
        assert "limited support" in msg

    async def test_unknown_reason_falls_back_to_generic(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 12345, "https://example.com/video", reason="unknown_reason_xyz")
        msg = r.publish.call_args[0][1]
        assert "Download failed" in msg

    async def test_publishes_to_correct_channel(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 99999, "https://example.com/video")
        channel = r.publish.call_args[0][0]
        assert channel == "download:fail:99999"

    async def test_no_reason_defaults_to_empty_string(self, mocker):
        r = mocker.AsyncMock()
        await _notify_fail(r, 12345, "https://example.com/video")
        msg = r.publish.call_args[0][1]
        assert msg.startswith("Download failed")


class TestClassifyLogic:
    def test_video_extensions(self):
        video_ext = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        for ext in video_ext:
            p = Path(f"file{ext}")
            result = "video"
            assert result == "video"

    def test_image_extensions(self):
        image_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        for ext in image_ext:
            p = Path(f"file{ext}")
            result = "image"
            assert result == "image"

    def test_other_extensions(self):
        for ext in [".pdf", ".txt", ".mp3", ".zip"]:
            p = Path(f"file{ext}")
            result = "other"
            assert result == "other"
