from pathlib import Path

import pytest

from p9qemu.drawterm_password_rotation import (
    NEW_PASSWORD_MARKER,
    OLD_PASSWORD_MARKER,
    ROTATION_MARKER,
    build_new_password_probe_command,
    build_old_password_probe_command,
    build_rotation_guest_command,
    build_rotation_shutdown_command,
    build_wrkey_input,
    generate_rotation_password,
    require_passwords_absent,
    validate_mutation_output,
    validate_new_password_acceptance,
    validate_old_password_rejection,
    validate_rotation_password,
)
from p9qemu.drawterm_postinstall import load_drawterm_postinstall_profile
from p9qemu.errors import P9QemuError


PROFILE_PATH = (
    Path(__file__).parents[1]
    / "images"
    / "p9qemu-9front-11554-amd64-hjfs-gmt-drawterm-001"
    / "postinstall.json"
)
NEW_PASSWORD = "0123456789abcdef01234567"


def profile():
    return load_drawterm_postinstall_profile(PROFILE_PATH)


def passwords() -> tuple[str, str]:
    return profile().nvram.password, NEW_PASSWORD


def test_generated_password_is_bounded_hex_and_distinct() -> None:
    current = profile().nvram.password
    generated = generate_rotation_password(current)
    assert len(generated) == 24
    assert generated != current
    assert set(generated) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    "candidate",
    ("short", "A" * 24, "0" * 23, "0" * 25, "0" * 24 + "\n"),
)
def test_rotation_password_has_one_strict_safe_format(candidate: str) -> None:
    with pytest.raises(P9QemuError, match="24 lowercase hexadecimal"):
        validate_rotation_password(candidate, profile().nvram.password)


def test_wrkey_stdin_matches_the_six_release_prompts() -> None:
    value = profile()
    assert build_wrkey_input(value, NEW_PASSWORD) == (
        f"glenda\n9front\n\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n\n"
    )


def test_guest_commands_are_bounded_and_do_not_contain_passwords() -> None:
    commands = (
        build_rotation_guest_command(),
        build_old_password_probe_command(),
        build_new_password_probe_command(),
        build_rotation_shutdown_command(),
    )
    assert all(len(command) < 128 for command in commands)
    rendered = "\n".join(commands)
    assert profile().nvram.password not in rendered
    assert NEW_PASSWORD not in rendered
    assert ROTATION_MARKER in commands[0]
    assert OLD_PASSWORD_MARKER in commands[1]
    assert NEW_PASSWORD_MARKER in commands[2]


def test_mutation_marker_proves_wrkey_completed_even_if_fshalt_closes_drawterm() -> (
    None
):
    checks = validate_mutation_output(
        1,
        f"authid: glenda\nauthdom: 9front\n{ROTATION_MARKER}\n",
        "drawterm: cpu connection closed\n",
        passwords=passwords(),
    )
    assert [check.name for check in checks] == [
        "nvram-password-write",
        "mutation-session-exit",
    ]


def test_mutation_requires_success_marker() -> None:
    with pytest.raises(P9QemuError, match="did not emit"):
        validate_mutation_output(1, "", "", passwords=passwords())


def test_old_password_requires_explicit_drawterm_rejection() -> None:
    check = validate_old_password_rejection(
        1,
        "?password mismatch with auth server\n",
        "drawterm: wrong password\n",
        passwords=passwords(),
    )
    assert check.name == "old-password-rejected"


def test_old_password_success_is_rejected() -> None:
    with pytest.raises(P9QemuError, match="still authenticates"):
        validate_old_password_rejection(
            0,
            f"{OLD_PASSWORD_MARKER}\n",
            "",
            passwords=passwords(),
        )


def test_old_password_non_auth_failure_is_not_false_success() -> None:
    with pytest.raises(P9QemuError, match="authentication-rejection signature"):
        validate_old_password_rejection(
            1,
            "",
            "cannot read p9any negotiation: hung up",
            passwords=passwords(),
        )


def test_new_password_requires_authenticated_marker() -> None:
    check = validate_new_password_acceptance(
        0,
        f"{NEW_PASSWORD_MARKER}\n",
        "",
        passwords=passwords(),
    )
    assert check.name == "new-password-accepted"


@pytest.mark.parametrize("returncode,stdout", ((1, NEW_PASSWORD_MARKER), (0, "")))
def test_new_password_rejects_failure_or_missing_marker(
    returncode: int, stdout: str
) -> None:
    with pytest.raises(P9QemuError):
        validate_new_password_acceptance(
            returncode,
            stdout,
            "",
            passwords=passwords(),
        )


@pytest.mark.parametrize("secret", ("p9qemu-demo", NEW_PASSWORD))
def test_no_password_may_enter_evidence(secret: str) -> None:
    with pytest.raises(P9QemuError, match="exposed password material"):
        require_passwords_absent(passwords(), f"prefix {secret} suffix", label="log")
