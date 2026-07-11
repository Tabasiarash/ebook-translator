from __future__ import annotations

from pathlib import Path

from ebook_translator.tts import TTS_VOICE_MAP, _concatenate_mp3, _estimate_duration, voice_for


class TestVoiceFor:
    def test_known_language_returns_correct_voice(self):
        assert voice_for("Farsi") == "fa-IR-DilaraNeural"
        assert voice_for("Arabic") == "ar-SA-ZariyahNeural"
        assert voice_for("Spanish") == "es-ES-ElviraNeural"
        assert voice_for("French") == "fr-FR-DeniseNeural"
        assert voice_for("German") == "de-DE-KatjaNeural"

    def test_persian_alias_maps_same_as_farsi(self):
        assert voice_for("Persian") == voice_for("Farsi")

    def test_unknown_language_falls_back_to_default(self):
        assert voice_for("Swahili") == "en-US-AriaNeural"
        assert voice_for("UnknownLang") == "en-US-AriaNeural"

    def test_all_voices_are_non_empty(self):
        for lang, voice in TTS_VOICE_MAP.items():
            assert len(voice) > 0, f"{lang} has empty voice"

    def test_default_key_exists(self):
        assert "default" in TTS_VOICE_MAP
        assert TTS_VOICE_MAP["default"] == "en-US-AriaNeural"


class TestEstimateDuration:
    def test_short_text(self):
        chunks = [{"translated_text": "Hello world"}]
        assert _estimate_duration(1, chunks) >= 1

    def test_longer_text(self):
        text = "word " * 450
        chunks = [{"translated_text": text.strip()}]
        duration = _estimate_duration(1, chunks)
        assert duration >= 1
        assert duration < 60

    def test_uses_translated_text_first(self):
        short = [{"translated_text": "A" * 100, "source_text": "Z" * 5000}]
        long = [{"translated_text": "Z" * 5000, "source_text": "A" * 100}]
        d_short = _estimate_duration(1, short)
        d_long = _estimate_duration(1, long)
        assert d_short < d_long

    def test_falls_back_to_source_text(self):
        chunks = [{"source_text": "Hello world this is a test"}]
        duration = _estimate_duration(1, chunks)
        assert duration >= 1

    def test_empty_chunks(self):
        assert _estimate_duration(0, []) >= 1

    def test_does_not_overflow_with_large_text(self):
        chunks = [{"translated_text": "word " * 100000}]
        duration = _estimate_duration(1, chunks)
        assert isinstance(duration, int)
        assert duration > 0


class TestConcatenateMp3:
    async def test_no_segments_returns_empty_file(self, tmp_path):
        output = tmp_path / "combined.mp3"
        result = await _concatenate_mp3([], output)
        assert result == output
        assert output.read_bytes() == b""

    async def test_single_segment(self, tmp_path):
        seg = tmp_path / "seg.mp3"
        seg.write_bytes(b"\xff\xfb\x90\x00")
        output = tmp_path / "combined.mp3"
        result = await _concatenate_mp3([seg], output)
        assert result == output
        assert output.read_bytes() == b"\xff\xfb\x90\x00"

    async def test_multiple_segments_concatenated_in_order(self, tmp_path):
        seg1 = tmp_path / "seg_1.mp3"
        seg2 = tmp_path / "seg_2.mp3"
        seg1.write_bytes(b"FIRST")
        seg2.write_bytes(b"SECOND")
        output = tmp_path / "combined.mp3"
        result = await _concatenate_mp3([seg1, seg2], output)
        assert result == output
        assert output.read_bytes() == b"FIRSTSECOND"

    async def test_output_is_binary_concatenation(self, tmp_path):
        segs = []
        for i in range(3):
            p = tmp_path / f"seg_{i}.mp3"
            p.write_bytes(bytes([i] * 100))
            segs.append(p)
        output = tmp_path / "combined.mp3"
        await _concatenate_mp3(segs, output)
        assert len(output.read_bytes()) == 300
