from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from bot.handlers.video_download import URL_PATTERN, SUPPORTED_DOMAINS, get_domain_engine


class TestSupportedDomains:
    def test_youtube_domains(self):
        assert "youtube.com" in SUPPORTED_DOMAINS
        assert "youtu.be" in SUPPORTED_DOMAINS

    def test_instagram_domain(self):
        assert "instagram.com" in SUPPORTED_DOMAINS

    def test_twitter_domains(self):
        assert "twitter.com" in SUPPORTED_DOMAINS
        assert "x.com" in SUPPORTED_DOMAINS

    def test_facebook_domains(self):
        assert "facebook.com" in SUPPORTED_DOMAINS
        assert "fb.watch" in SUPPORTED_DOMAINS

    def test_tiktok_domain(self):
        assert "tiktok.com" in SUPPORTED_DOMAINS

    def test_linkedin_domain(self):
        assert "linkedin.com" in SUPPORTED_DOMAINS

    def test_snapchat_domain(self):
        assert "snapchat.com" in SUPPORTED_DOMAINS


class TestUrlPattern:
    def test_matches_youtube(self):
        assert URL_PATTERN.search("https://www.youtube.com/watch?v=abc123")

    def test_matches_youtu_be(self):
        assert URL_PATTERN.search("https://youtu.be/abc123")

    def test_matches_instagram(self):
        assert URL_PATTERN.search("https://www.instagram.com/p/abc123/")
        assert URL_PATTERN.search("https://instagram.com/reel/abc123/")

    def test_matches_twitter(self):
        assert URL_PATTERN.search("https://twitter.com/user/status/123456789")
        assert URL_PATTERN.search("https://x.com/user/status/123456789")

    def test_matches_facebook(self):
        assert URL_PATTERN.search("https://www.facebook.com/watch?v=abc123")
        assert URL_PATTERN.search("https://fb.watch/abc123/")

    def test_matches_tiktok(self):
        assert URL_PATTERN.search("https://www.tiktok.com/@user/video/123456789")

    def test_matches_linkedin(self):
        assert URL_PATTERN.search("https://www.linkedin.com/posts/user-video-123456")

    def test_matches_snapchat(self):
        assert URL_PATTERN.search("https://www.snapchat.com/t/abc123")

    def test_does_not_match_unsupported(self):
        assert not URL_PATTERN.search("https://vimeo.com/12345")
        assert not URL_PATTERN.search("https://example.com")
        assert not URL_PATTERN.search("plain text no url")

    def test_extracts_full_url(self):
        text = "Check this out https://www.youtube.com/watch?v=abc123 more text"
        m = URL_PATTERN.search(text)
        assert m is not None
        assert m.group(0) == "https://www.youtube.com/watch?v=abc123"

    def test_matches_without_scheme(self):
        assert URL_PATTERN.search("youtube.com/watch?v=abc123")


class TestGetDomainEngine:
    def test_youtube_returns_ytdlp(self):
        assert get_domain_engine("youtube.com") == "yt-dlp"
        assert get_domain_engine("youtu.be") == "yt-dlp"

    def test_instagram_returns_gallery_dl(self):
        assert get_domain_engine("instagram.com") == "gallery-dl"

    def test_twitter_returns_twitter(self):
        assert get_domain_engine("twitter.com") == "twitter"
        assert get_domain_engine("x.com") == "twitter"

    def test_facebook_returns_ytdlp(self):
        assert get_domain_engine("facebook.com") == "yt-dlp"
        assert get_domain_engine("fb.watch") == "yt-dlp"

    def test_tiktok_returns_ytdlp(self):
        assert get_domain_engine("tiktok.com") == "yt-dlp"

    def test_linkedin_returns_ytdlp(self):
        assert get_domain_engine("linkedin.com") == "yt-dlp"

    def test_snapchat_returns_ytdlp(self):
        assert get_domain_engine("snapchat.com") == "yt-dlp"

    def test_unsupported_returns_unsupported(self):
        assert get_domain_engine("vimeo.com") == "unsupported"
        assert get_domain_engine("example.com") == "unsupported"

    def test_handles_www_prefix(self):
        assert get_domain_engine("www.youtube.com") == "unsupported"
        assert get_domain_engine("www.instagram.com") == "unsupported"

    def test_domain_extraction_from_url(self):
        urls = [
            ("https://www.youtube.com/watch?v=abc123", "youtube.com", "yt-dlp"),
            ("https://youtu.be/abc123", "youtu.be", "yt-dlp"),
            ("https://www.instagram.com/p/abc123/", "instagram.com", "gallery-dl"),
            ("https://x.com/user/status/123", "x.com", "twitter"),
            ("https://www.facebook.com/watch?v=abc123", "facebook.com", "yt-dlp"),
            ("https://fb.watch/abc123/", "fb.watch", "yt-dlp"),
            ("https://www.tiktok.com/@user/video/123", "tiktok.com", "yt-dlp"),
            ("https://www.linkedin.com/posts/user", "linkedin.com", "yt-dlp"),
            ("https://www.snapchat.com/t/abc123", "snapchat.com", "yt-dlp"),
        ]
        for url, expected_domain, expected_engine in urls:
            domain = urlparse(url).netloc.replace("www.", "")
            assert domain == expected_domain
            assert get_domain_engine(domain) == expected_engine
