"""Tests for pipeline.tag_filter — Last.fm tag noise filtering."""

from __future__ import annotations

import pytest

from pipeline.tag_filter import (
    build_artist_block,
    filter_tags,
    is_noise_tag,
)


# ── radio stations ──


@pytest.mark.parametrize(
    "tag",
    [
        "wsum 91.7 fm madison",
        "88.5 fm",
        "1010 am",
        "KEXP 90.3 FM",
    ],
)
def test_radio_station_tags_blocked(tag):
    assert is_noise_tag(tag) is True


def test_non_station_with_am_not_blocked():
    # No leading digits → not a frequency. "3am" has a single digit.
    assert is_noise_tag("3am") is False
    assert is_noise_tag("miami") is False


# ── specific years vs decades ──


@pytest.mark.parametrize("tag", ["2016", "2022", "1999", "1975", "2025"])
def test_specific_years_blocked(tag):
    assert is_noise_tag(tag) is True


@pytest.mark.parametrize("tag", ["90s", "00s", "10s", "20s", "80s", "60s", "2010s", "2020s", "2000s"])
def test_decades_kept(tag):
    assert is_noise_tag(tag) is False


# ── "my …" personal-collection tags ──


@pytest.mark.parametrize(
    "tag",
    ["my top songs", "my favorites", "my favourites", "My Top Songs Of 2016"],
)
def test_my_tags_blocked(tag):
    assert is_noise_tag(tag) is True


def test_non_my_subjective_tags_kept():
    # Subjective reaction tags are fine — only "my …" ones go.
    for tag in ["beautiful", "amazing", "love at first listen", "masterpiece", "fire"]:
        assert is_noise_tag(tag) is False


# ── artist-name-as-tag ──


def test_artist_tags_blocked_against_block_set():
    block = frozenset({"kanye west", "drake", "muse", "weeknd"})
    assert is_noise_tag("kanye west", block) is True
    assert is_noise_tag("Drake", block) is True
    assert is_noise_tag("the weeknd", block) is True  # normalizes to "weeknd"
    assert is_noise_tag("Muse", block) is True


def test_artist_rule_skipped_without_block_set():
    assert is_noise_tag("kanye west") is False


def test_genre_collision_with_artist_is_protected():
    # Even if a genre/mood word is in the artist block, it must survive.
    block = frozenset({"love", "soul", "future", "rap"})
    for tag in ["love", "soul", "future", "rap"]:
        assert is_noise_tag(tag, block) is False


# ── empties / junk ──


@pytest.mark.parametrize("tag", ["", "   ", None, 123, []])
def test_empty_or_nonstring_dropped(tag):
    assert is_noise_tag(tag) is True


# ── filter_tags integration ──


def test_filter_tags_preserves_order_and_originals():
    block = frozenset({"kanye west", "drake"})
    tags = [
        "Hip-Hop",
        "kanye west",
        "2016",
        "conscious hip hop",
        "my top songs",
        "wsum 91.7 fm madison",
        "90s",
        "rap",
    ]
    assert filter_tags(tags, block) == [
        "Hip-Hop",
        "conscious hip hop",
        "90s",
        "rap",
    ]


def test_filter_tags_empty_input():
    assert filter_tags(None) == []
    assert filter_tags([]) == []


# ── build_artist_block ──


def test_build_artist_block_uses_normalized_field():
    tracks = [
        {"artist_normalized": "kanye west"},
        {"artist": "The Weeknd"},  # falls back to normalize_artist → "weeknd"
        {"artist_normalized": "x"},  # too short → dropped
    ]
    block = build_artist_block(tracks)
    assert "kanye west" in block
    assert "weeknd" in block
    assert "x" not in block


def test_build_artist_block_excludes_protected_words():
    tracks = [{"artist_normalized": "love"}, {"artist_normalized": "muse"}]
    block = build_artist_block(tracks)
    assert "love" not in block  # protected
    assert "muse" in block
