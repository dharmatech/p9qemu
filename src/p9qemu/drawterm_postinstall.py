"""Strict, transport-independent preparation of the Drawterm image variant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from p9qemu.errors import P9QemuError
from p9qemu.validation import GuestValidationTransport, SHELL_PROMPT


DRAWTERM_POSTINSTALL_SCHEMA = 1
DRAWTERM_POSTINSTALL_KIND = "p9qemu-9front-postinstall"
DRAWTERM_PROFILE_V1 = "9front-11554-amd64-hjfs-gmt-drawterm-v1"
STAGED_PLAN9_INI = "/tmp/p9qemu-drawterm-plan9.ini"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_SOURCE_SETTINGS = (
    "bootfile",
    "bootargs",
    "mouseport",
    "monitor",
    "vgasize",
    "console",
)
_ADDED_SETTINGS = ("nobootprompt", "nvram", "service")
_REQUIRED_TARGET_SETTINGS = _REQUIRED_SOURCE_SETTINGS + _ADDED_SETTINGS


@dataclass(frozen=True)
class ParentReadyImage:
    """Immutable ready image from which the derivative must be built."""

    image_id: str
    manifest_url: str
    manifest_sha256: str
    image_sha256: str


@dataclass(frozen=True)
class DrawtermGuest:
    """Guest identity expected before and after the post-install step."""

    user: str
    system_name: str
    root_partition: str


@dataclass(frozen=True)
class Plan9IniTransition:
    """Exact plan9.ini state transition for the Drawterm variant."""

    path: str
    source_required: tuple[tuple[str, str], ...]
    source_absent: tuple[str, ...]
    target_required: tuple[tuple[str, str], ...]

    @property
    def source_values(self) -> tuple[str, ...]:
        return tuple(f"{name}={value}" for name, value in self.source_required)

    @property
    def target_values(self) -> tuple[str, ...]:
        return tuple(f"{name}={value}" for name, value in self.target_required)

    @property
    def additions(self) -> tuple[str, ...]:
        source_names = {name for name, _value in self.source_required}
        return tuple(
            f"{name}={value}"
            for name, value in self.target_required
            if name not in source_names
        )


@dataclass(frozen=True)
class NvramConfiguration:
    """Inputs to the fixed auth/wrkey interaction."""

    method: str
    authid: str
    authdom: str
    secstore_key: str
    password: str
    credential_class: str
    legacy_p9sk1: bool


@dataclass(frozen=True)
class DrawtermEndpoint:
    """Loopback-only host ports used by the documented Drawterm command."""

    bind_address: str
    cpu_host_port: int
    auth_host_port: int


@dataclass(frozen=True)
class DrawtermPostinstallProfile:
    """One exact, qualified post-install derivative profile."""

    schema: int
    kind: str
    profile_id: str
    parent: ParentReadyImage
    guest: DrawtermGuest
    plan9_ini: Plan9IniTransition
    nvram: NvramConfiguration
    drawterm: DrawtermEndpoint


@dataclass(frozen=True)
class DrawtermPreparationResult:
    """Verified plan9.ini state before and after preparation."""

    before: str
    after: str


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise P9QemuError(
            f"Drawterm post-install profile requires an object at {label}"
        )
    return value


def _exact_keys(document: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(document)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise P9QemuError(
            f"Drawterm post-install fields differ at {label}; "
            f"missing={missing}, unknown={unknown}"
        )


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        raise P9QemuError(f"Drawterm post-install profile requires text at {label}")
    if not allow_empty and not value:
        raise P9QemuError(
            f"Drawterm post-install profile requires non-empty text at {label}"
        )
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise P9QemuError(
            f"Drawterm post-install text contains an unsupported character at {label}"
        )
    return value


def _sha256(value: object, label: str) -> str:
    text = _text(value, label)
    if not _SHA256.fullmatch(text):
        raise P9QemuError(
            f"Drawterm post-install profile requires a lowercase SHA-256 at {label}"
        )
    return text


def _positive_port(value: object, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise P9QemuError(
            f"Drawterm post-install profile requires a TCP port at {label}"
        )
    return value


def _settings(
    value: object, names: tuple[str, ...], label: str
) -> tuple[tuple[str, str], ...]:
    document = _object(value, label)
    _exact_keys(document, set(names), label)
    return tuple((name, _text(document.get(name), f"{label}.{name}")) for name in names)


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise P9QemuError(f"Drawterm post-install profile requires a list at {label}")
    result = tuple(_text(item, f"{label}[]") for item in value)
    if len(result) != len(set(result)):
        raise P9QemuError(f"Drawterm post-install profile has duplicates at {label}")
    return result


def parse_drawterm_postinstall_profile(
    document: Mapping[str, Any],
) -> DrawtermPostinstallProfile:
    """Strictly parse the first qualified Drawterm post-install profile."""

    _exact_keys(
        document,
        {
            "schema",
            "kind",
            "profile_id",
            "parent",
            "guest",
            "plan9_ini",
            "nvram",
            "drawterm",
        },
        "root",
    )

    parent_document = _object(document.get("parent"), "parent")
    _exact_keys(
        parent_document,
        {"image_id", "manifest_url", "manifest_sha256", "image_sha256"},
        "parent",
    )
    guest_document = _object(document.get("guest"), "guest")
    _exact_keys(guest_document, {"user", "system_name", "root_partition"}, "guest")
    plan9_ini_document = _object(document.get("plan9_ini"), "plan9_ini")
    _exact_keys(plan9_ini_document, {"path", "source", "target"}, "plan9_ini")
    source_document = _object(plan9_ini_document.get("source"), "plan9_ini.source")
    _exact_keys(source_document, {"required", "absent"}, "plan9_ini.source")
    target_document = _object(plan9_ini_document.get("target"), "plan9_ini.target")
    _exact_keys(target_document, {"required"}, "plan9_ini.target")
    nvram_document = _object(document.get("nvram"), "nvram")
    _exact_keys(
        nvram_document,
        {
            "method",
            "authid",
            "authdom",
            "secstore_key",
            "password",
            "credential_class",
            "legacy_p9sk1",
        },
        "nvram",
    )
    drawterm_document = _object(document.get("drawterm"), "drawterm")
    _exact_keys(
        drawterm_document,
        {"bind_address", "cpu_host_port", "auth_host_port"},
        "drawterm",
    )

    schema = document.get("schema")
    if type(schema) is not int:
        raise P9QemuError("Drawterm post-install profile schema must be int")
    legacy_p9sk1 = nvram_document.get("legacy_p9sk1")
    if type(legacy_p9sk1) is not bool:
        raise P9QemuError("Drawterm post-install nvram.legacy_p9sk1 must be bool")

    profile = DrawtermPostinstallProfile(
        schema=schema,
        kind=_text(document.get("kind"), "kind"),
        profile_id=_text(document.get("profile_id"), "profile_id"),
        parent=ParentReadyImage(
            image_id=_text(parent_document.get("image_id"), "parent.image_id"),
            manifest_url=_text(
                parent_document.get("manifest_url"), "parent.manifest_url"
            ),
            manifest_sha256=_sha256(
                parent_document.get("manifest_sha256"), "parent.manifest_sha256"
            ),
            image_sha256=_sha256(
                parent_document.get("image_sha256"), "parent.image_sha256"
            ),
        ),
        guest=DrawtermGuest(
            user=_text(guest_document.get("user"), "guest.user"),
            system_name=_text(guest_document.get("system_name"), "guest.system_name"),
            root_partition=_text(
                guest_document.get("root_partition"), "guest.root_partition"
            ),
        ),
        plan9_ini=Plan9IniTransition(
            path=_text(plan9_ini_document.get("path"), "plan9_ini.path"),
            source_required=_settings(
                source_document.get("required"),
                _REQUIRED_SOURCE_SETTINGS,
                "plan9_ini.source.required",
            ),
            source_absent=_string_list(
                source_document.get("absent"), "plan9_ini.source.absent"
            ),
            target_required=_settings(
                target_document.get("required"),
                _REQUIRED_TARGET_SETTINGS,
                "plan9_ini.target.required",
            ),
        ),
        nvram=NvramConfiguration(
            method=_text(nvram_document.get("method"), "nvram.method"),
            authid=_text(nvram_document.get("authid"), "nvram.authid"),
            authdom=_text(nvram_document.get("authdom"), "nvram.authdom"),
            secstore_key=_text(
                nvram_document.get("secstore_key"),
                "nvram.secstore_key",
                allow_empty=True,
            ),
            password=_text(nvram_document.get("password"), "nvram.password"),
            credential_class=_text(
                nvram_document.get("credential_class"), "nvram.credential_class"
            ),
            legacy_p9sk1=legacy_p9sk1,
        ),
        drawterm=DrawtermEndpoint(
            bind_address=_text(
                drawterm_document.get("bind_address"), "drawterm.bind_address"
            ),
            cpu_host_port=_positive_port(
                drawterm_document.get("cpu_host_port"), "drawterm.cpu_host_port"
            ),
            auth_host_port=_positive_port(
                drawterm_document.get("auth_host_port"), "drawterm.auth_host_port"
            ),
        ),
    )

    expected = DrawtermPostinstallProfile(
        schema=DRAWTERM_POSTINSTALL_SCHEMA,
        kind=DRAWTERM_POSTINSTALL_KIND,
        profile_id=DRAWTERM_PROFILE_V1,
        parent=ParentReadyImage(
            image_id="p9qemu-9front-11554-amd64-hjfs-gmt-002",
            manifest_url=(
                "https://github.com/dharmatech/p9qemu/releases/download/"
                "ready-9front-11554-amd64-hjfs-gmt-002/image.json"
            ),
            manifest_sha256=(
                "cfee07ec6fcf82d15ce77b43d8633f696e92118f8cff166a766ccdc9c05dfc53"
            ),
            image_sha256=(
                "1ef80c81a3f2dd09d2f173ff7dfa93d07ecee2ba453fc0f0964190adb6ee44a8"
            ),
        ),
        guest=DrawtermGuest(
            user="glenda",
            system_name="cirno",
            root_partition="/dev/sd00/fs",
        ),
        plan9_ini=Plan9IniTransition(
            path="/n/9fat/plan9.ini",
            source_required=(
                ("bootfile", "9pc64"),
                ("bootargs", "local!/dev/sd00/fs -m 147"),
                ("mouseport", "ps2"),
                ("monitor", "vesa"),
                ("vgasize", "1024x768x16"),
                ("console", "0"),
            ),
            source_absent=_ADDED_SETTINGS,
            target_required=(
                ("bootfile", "9pc64"),
                ("bootargs", "local!/dev/sd00/fs -m 147"),
                ("mouseport", "ps2"),
                ("monitor", "vesa"),
                ("vgasize", "1024x768x16"),
                ("console", "0"),
                ("nobootprompt", "local!/dev/sd00/fs -m 147"),
                ("nvram", "#S/sd00/nvram"),
                ("service", "cpu"),
            ),
        ),
        nvram=NvramConfiguration(
            method="auth-wrkey-v1",
            authid="glenda",
            authdom="9front",
            secstore_key="",
            password="p9qemu-demo",
            credential_class="public-demo",
            legacy_p9sk1=False,
        ),
        drawterm=DrawtermEndpoint(
            bind_address="127.0.0.1",
            cpu_host_port=17019,
            auth_host_port=17567,
        ),
    )
    if profile != expected:
        raise P9QemuError(
            f"unsupported Drawterm post-install profile {profile.profile_id!r} or values"
        )
    return profile


def load_drawterm_postinstall_profile(path: Path) -> DrawtermPostinstallProfile:
    """Load and strictly validate a Drawterm post-install JSON profile."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise P9QemuError(
            f"could not read Drawterm post-install profile {path}: {error}"
        ) from error
    if not isinstance(document, dict):
        raise P9QemuError("Drawterm post-install profile root must be an object")
    return parse_drawterm_postinstall_profile(document)


def _observed_settings(
    output: str, profile: DrawtermPostinstallProfile, *, state: str
) -> dict[str, list[str]]:
    names = tuple(name for name, _value in profile.plan9_ini.target_required)
    found: dict[str, list[str]] = {name: [] for name in names}
    pattern = re.compile(rf"^({'|'.join(re.escape(name) for name in names)})=(.*)$")
    for line in output.replace("\r", "").splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            found[match.group(1)].append(match.group(2))
    duplicates = sorted(name for name, values in found.items() if len(values) > 1)
    if duplicates:
        raise P9QemuError(f"{state} has duplicate controlled settings: {duplicates}")
    return found


def _require_source(output: str, profile: DrawtermPostinstallProfile) -> None:
    observed = _observed_settings(output, profile, state="source plan9.ini")
    required = dict(profile.plan9_ini.source_required)
    missing = sorted(name for name in required if not observed[name])
    unexpected = {
        name: values[0]
        for name, values in observed.items()
        if name in required and values and values[0] != required[name]
    }
    present = sorted(name for name in profile.plan9_ini.source_absent if observed[name])
    if missing or unexpected or present:
        raise P9QemuError(
            "source plan9.ini does not match the pinned stock image; "
            f"missing={missing}, unexpected={unexpected}, expected_absent_but_present={present}"
        )


def _require_target(
    output: str, profile: DrawtermPostinstallProfile, *, state: str
) -> None:
    observed = _observed_settings(output, profile, state=state)
    required = dict(profile.plan9_ini.target_required)
    missing = sorted(name for name in required if not observed[name])
    unexpected = {
        name: values[0]
        for name, values in observed.items()
        if values and values[0] != required[name]
    }
    if missing or unexpected:
        raise P9QemuError(
            f"{state} does not match the qualified Drawterm profile; "
            f"missing={missing}, unexpected={unexpected}"
        )


def _append_command(profile: DrawtermPostinstallProfile) -> str:
    commands = [f"cp {profile.plan9_ini.path} {STAGED_PLAN9_INI}"]
    commands.extend(
        f"echo '{value}' >>{STAGED_PLAN9_INI}" for value in profile.plan9_ini.additions
    )
    return "; ".join(commands)


def _expected_after(before: str, profile: DrawtermPostinstallProfile) -> str:
    original = before.replace("\r", "").rstrip("\n")
    return original + "\n" + "\n".join(profile.plan9_ini.additions) + "\n"


def _command_output_lines(output: str, command: str) -> list[str]:
    lines = output.replace("\r", "").splitlines()
    if lines and lines[0].strip() == command:
        lines = lines[1:]
    return [line.strip() for line in lines if line.strip()]


def _command_output_text(output: str, command: str) -> str:
    lines = output.replace("\r", "").splitlines(keepends=True)
    if lines and lines[0].strip() == command:
        lines = lines[1:]
    return "".join(lines)


def drive_drawterm_preparation(
    transport: GuestValidationTransport,
    profile: DrawtermPostinstallProfile,
) -> DrawtermPreparationResult:
    """Apply and verify the qualified Drawterm post-install profile."""

    transport.wait("boot.bootargs", r"bootargs is .*?\[[^\]\n]+\][ \t]*", 120)
    transport.send_line("")
    transport.wait("boot.user", re.escape(f"user[{profile.guest.user}]:"), 120)
    transport.send_line(profile.guest.user)
    transport.wait(
        "boot.root", re.escape(f"hjfs: fs is {profile.guest.root_partition}"), 120
    )
    transport.wait("boot.shell", SHELL_PROMPT, 120)
    transport.command("guest.mount-9fat", "9fs 9fat", SHELL_PROMPT, 60)

    before_command = f"cat {profile.plan9_ini.path}"
    before = _command_output_text(
        transport.command(
            "guest.plan9-ini-before",
            before_command,
            SHELL_PROMPT,
            60,
        ),
        before_command,
    )
    _require_source(before, profile)
    if not before.endswith("\n"):
        raise P9QemuError("source plan9.ini must end with a newline")

    nvram_probe = "test -e /dev/sd00/nvram && echo P9QEMU_NVRAM_READY"
    probe_output = transport.command(
        "guest.nvram-partition", nvram_probe, SHELL_PROMPT, 60
    )
    if "P9QEMU_NVRAM_READY" not in _command_output_lines(probe_output, nvram_probe):
        raise P9QemuError("the pinned NVRAM partition is not available")

    append_command = _append_command(profile)
    transport.command("guest.stage-plan9-ini", append_command, SHELL_PROMPT, 60)
    staged_command = f"cat {STAGED_PLAN9_INI}"
    staged = _command_output_text(
        transport.command(
            "guest.plan9-ini-staged",
            staged_command,
            SHELL_PROMPT,
            60,
        ),
        staged_command,
    )
    _require_target(staged, profile, state="staged plan9.ini")
    expected_after = _expected_after(before, profile)
    if staged != expected_after:
        raise P9QemuError(
            "staged plan9.ini changed content outside the qualified additions"
        )

    transport.command(
        "guest.install-plan9-ini",
        f"cp {STAGED_PLAN9_INI} {profile.plan9_ini.path}",
        SHELL_PROMPT,
        60,
    )
    after_command = f"cat {profile.plan9_ini.path}"
    after = _command_output_text(
        transport.command(
            "guest.plan9-ini-after",
            after_command,
            SHELL_PROMPT,
            60,
        ),
        after_command,
    )
    _require_target(after, profile, state="installed plan9.ini")
    if after != expected_after:
        raise P9QemuError(
            "installed plan9.ini changed content outside the qualified additions"
        )

    transport.send_line("auth/wrkey")
    transport.wait("guest.wrkey-authid", r"authid:[ \t]*", 60)
    transport.send_line(profile.nvram.authid)
    transport.wait("guest.wrkey-authdom", r"authdom:[ \t]*", 60)
    transport.send_line(profile.nvram.authdom)
    transport.wait("guest.wrkey-secstore", r"secstore key:[ \t]*", 60)
    transport.send_line(profile.nvram.secstore_key)
    transport.wait("guest.wrkey-password", r"password:[ \t]*", 60)
    transport.send_line(profile.nvram.password)
    transport.wait("guest.wrkey-confirm-password", r"confirm password:[ \t]*", 60)
    transport.send_line(profile.nvram.password)
    transport.wait(
        "guest.wrkey-legacy-p9sk1",
        r"enable legacy p9sk1\[no\]:[ \t]*",
        60,
    )
    transport.send_line("yes" if profile.nvram.legacy_p9sk1 else "")
    transport.wait("guest.wrkey-complete", SHELL_PROMPT, 60)
    status_command = "echo $status"
    status_output = transport.command(
        "guest.wrkey-status", status_command, SHELL_PROMPT, 60
    )
    status_lines = _command_output_lines(status_output, status_command)
    if status_lines:
        raise P9QemuError(f"auth/wrkey failed with status: {' '.join(status_lines)}")

    transport.command(
        "guest.remove-temporary", f"rm {STAGED_PLAN9_INI}", SHELL_PROMPT, 60
    )
    transport.send_line("fshalt")
    transport.wait("shutdown.fshalt", re.escape("done halting"), 120)
    return DrawtermPreparationResult(before=before, after=after)
