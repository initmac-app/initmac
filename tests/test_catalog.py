import copy

import pytest

from catalog.validate import CatalogError, load_all, validate


def test_shipped_data_is_valid():
    apps, questionnaire, tweaks = load_all()
    assert len(apps) > 40
    assert {c["id"] for c in questionnaire["categories"]} == {a["category"] for a in apps}
    assert tweaks


def test_every_category_has_a_core_app():
    apps, questionnaire, _ = load_all()
    for cat in questionnaire["categories"]:
        assert any(a["core"] for a in apps if a["category"] == cat["id"]), cat["id"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda apps: apps.append(copy.deepcopy(apps[0])), "duplicate"),
        (lambda apps: apps[0].update(type="pkg"), "type"),
        (lambda apps: apps[0].update(category="nope"), "category"),
        (lambda apps: apps[0].update(audiences=["role_astronaut"]), "audiences"),
        (lambda apps: apps[0].update(why=""), "why"),
    ],
)
def test_validate_rejects_bad_apps(mutate, message):
    apps, questionnaire, tweaks = copy.deepcopy(load_all())
    mutate(apps)
    with pytest.raises(CatalogError, match=message):
        validate(apps, questionnaire, tweaks)


# ---- safety of what the catalog can make the installer do ----------------------
import re  # noqa: E402

BREW_TOKEN = re.compile(r"^[a-z0-9][a-z0-9@+._-]*$")
ALLOWED_TAPS = {"hashicorp/tap"}
ALLOWED_DEFAULTS_DOMAINS = {
    "com.apple.dock", "NSGlobalDomain", "com.apple.finder", "com.apple.screencapture",
    "com.apple.AppleMultitouchTrackpad", "com.apple.driver.AppleBluetoothMultitouch.trackpad",
}


def test_brew_tokens_are_plain_names_from_trusted_taps():
    for app in load_all()[0]:
        parts = app["brew"].split("/")
        assert len(parts) in (1, 3), app["brew"]
        assert BREW_TOKEN.match(parts[-1]), app["brew"]
        if len(parts) == 3:
            assert "/".join(parts[:2]) in ALLOWED_TAPS, f"untrusted tap: {app['brew']}"
        assert app["homepage"].startswith("https://"), app["id"]


def test_macos_tweaks_only_touch_known_settings():
    for tweak in load_all()[2]:
        for domain, key, typ, value in tweak["writes"]:
            assert domain in ALLOWED_DEFAULTS_DOMAINS, f"{tweak['id']}: {domain}"
            assert re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key), f"{tweak['id']}: {key}"
        assert set(tweak.get("restart", [])) <= {"Dock", "Finder", "SystemUIServer"}
        if "mkdir" in tweak:
            assert tweak["mkdir"].startswith("~/") and ".." not in tweak["mkdir"]
