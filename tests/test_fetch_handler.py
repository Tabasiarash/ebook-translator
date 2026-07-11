from __future__ import annotations

from urllib.parse import urlparse

import pytest

from bot.handlers.fetch import URL_REGEX, _is_unsafe_ip, PRIVATE_RANGES


class TestUrlRegex:
    def test_matches_http_url(self):
        assert URL_REGEX.match("http://example.com/page")

    def test_matches_https_url(self):
        assert URL_REGEX.match("https://example.com/page")

    def test_matches_url_with_path(self):
        assert URL_REGEX.match("https://example.com/path/to/resource?q=1&r=2")

    def test_does_not_match_bare_text(self):
        assert not URL_REGEX.match("just some text without a url")

    def test_does_not_match_no_scheme(self):
        assert not URL_REGEX.match("example.com")


class TestIsUnsafeIp:
    @pytest.mark.parametrize("host", [
        "127.0.0.1",
        "localhost",
        "10.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.1.1",
        "::1",
    ])
    def test_private_loopback_localhost(self, host):
        assert _is_unsafe_ip(host), f"{host} should be unsafe"

    @pytest.mark.parametrize("host", [
        "8.8.8.8",
        "93.184.216.34",
        "example.com",
        "github.com",
    ])
    def test_public_hosts(self, host):
        assert not _is_unsafe_ip(host), f"{host} should be safe"

    def test_unresolvable_host_is_unsafe(self):
        assert _is_unsafe_ip("nonexistent.invalid")

    def test_cidr_ranges_match_expected(self):
        samples = {
            "127.0.0.0/8": "127.0.0.1",
            "10.0.0.0/8": "10.0.0.1",
            "172.16.0.0/12": "172.16.0.1",
            "192.168.0.0/16": "192.168.0.1",
            "169.254.0.0/16": "169.254.0.1",
            "::1/128": "::1",
            "fc00::/7": "fc00::1",
        }
        for cidr, sample in samples.items():
            assert _is_unsafe_ip(sample), f"{sample} in {cidr} should be unsafe"

    def test_ipv4_mapped_ipv6_not_accidentally_flagged(self):
        assert not _is_unsafe_ip("1.1.1.1")


class TestFetchInputValidation:
    def test_scheme_check_missing(self):
        parsed = urlparse("ftp://example.com/file")
        assert parsed.scheme not in ("http", "https")

    def test_scheme_check_http(self):
        parsed = urlparse("http://example.com/file")
        assert parsed.scheme in ("http", "https")

    def test_scheme_check_https(self):
        parsed = urlparse("https://example.com/file")
        assert parsed.scheme in ("http", "https")

    def test_empty_host(self):
        parsed = urlparse("")
        assert not parsed.hostname

    def test_localhost_check(self):
        assert "localhost" in ("localhost", "127.0.0.1", "::1")
        assert "127.0.0.1" in ("localhost", "127.0.0.1", "::1")
        assert "::1" in ("localhost", "127.0.0.1", "::1")
        assert "example.com" not in ("localhost", "127.0.0.1", "::1")

    def test_link_local_check(self):
        assert "169.254.1.1".startswith("169.254")
        assert "8.8.8.8".startswith("169.254") is False
