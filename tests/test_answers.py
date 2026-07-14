from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from p9qemu.answers import (
    ISO_SHA256_11554,
    PROFILE_ID_11554_HJFS,
    load_answers,
    parse_answers,
)
from p9qemu.errors import P9QemuError


ROOT = Path(__file__).parents[1]
REFERENCE_ANSWERS = (
    ROOT / "images" / "9front-11554-amd64-hjfs-manual-001" / "answers.toml"
)


def reference_document() -> dict:
    with REFERENCE_ANSWERS.open("rb") as stream:
        return tomllib.load(stream)


def test_reference_answer_file_is_the_supported_baseline() -> None:
    answers = load_answers(REFERENCE_ANSWERS)
    assert answers.installer_profile == PROFILE_ID_11554_HJFS
    assert answers.iso_sha256 == ISO_SHA256_11554
    assert answers.filesystem == "hjfs"
    assert answers.hjfs_partition == "/dev/sd00/fs"
    assert answers.timezone == "US_Pacific"


def test_unknown_top_level_key_is_rejected() -> None:
    document = reference_document()
    document["timezome"] = "US_Pacific"
    with pytest.raises(P9QemuError, match="unknown answer key.*timezome"):
        parse_answers(document)


def test_unknown_nested_key_is_rejected() -> None:
    document = reference_document()
    document["install"]["fileystem"] = "hjfs"
    with pytest.raises(P9QemuError, match="unknown answer key.*fileystem"):
        parse_answers(document)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("installer_profile", "latest", "installer_profile"),
        ("iso_sha256", "0" * 64, "iso_sha256"),
    ],
)
def test_profile_and_digest_must_match_the_qualified_media(
    key: str, value: str, message: str
) -> None:
    document = reference_document()
    document[key] = value
    with pytest.raises(P9QemuError, match=message):
        parse_answers(document)


@pytest.mark.parametrize(
    ("table", "key", "value", "message"),
    [
        ("disk", "target", "sd01", "disk.target"),
        ("disk", "use_entire_disk", False, "disk.use_entire_disk"),
        ("install", "filesystem", "gefs", "install.filesystem"),
        ("install", "timezone", "US_Eastern", "install.timezone"),
        ("network", "method", "manual", "network.method"),
    ],
)
def test_initial_profile_rejects_unqualified_variations(
    table: str, key: str, value: object, message: str
) -> None:
    document = reference_document()
    document[table][key] = value
    with pytest.raises(P9QemuError, match=message):
        parse_answers(document)


def test_boolean_is_not_accepted_for_integer_cache_size() -> None:
    document = reference_document()
    document["install"]["hjfs_cache_mib"] = True
    with pytest.raises(P9QemuError, match="hjfs_cache_mib must be int"):
        parse_answers(document)


def test_toml_syntax_error_names_the_answer_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("schema = [", encoding="utf-8")
    with pytest.raises(P9QemuError, match=r"could not read answer file .*broken.toml"):
        load_answers(path)
