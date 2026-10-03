"""Loads and validates the catalog JSON files that live next to this module."""

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

APP_TYPES = {"formula", "cask"}
DEFAULT_TYPES = {"bool", "int", "float", "string"}


class CatalogError(ValueError):
    pass


def _load(name: str):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def validate(apps: list[dict], questionnaire: dict, tweaks: list[dict]) -> None:
    category_ids = {c["id"] for c in questionnaire["categories"]}
    option_ids = {o["id"] for o in questionnaire["roles"] + questionnaire["activities"]}

    seen: set[str] = set()
    for app in apps:
        aid = app.get("id")
        if not aid or aid in seen:
            raise CatalogError(f"missing or duplicate app id: {aid!r}")
        seen.add(aid)
        for field in ("name", "brew", "type", "category", "description", "why", "audiences"):
            if not app.get(field):
                raise CatalogError(f"{aid}: missing {field}")
        if app["type"] not in APP_TYPES:
            raise CatalogError(f"{aid}: type must be one of {APP_TYPES}")
        if app["category"] not in category_ids:
            raise CatalogError(f"{aid}: unknown category {app['category']!r}")
        unknown = set(app["audiences"]) - option_ids
        if unknown:
            raise CatalogError(f"{aid}: unknown audiences {sorted(unknown)}")

    tweak_ids: set[str] = set()
    for tweak in tweaks:
        tid = tweak.get("id")
        if not tid or tid in tweak_ids:
            raise CatalogError(f"missing or duplicate defaults id: {tid!r}")
        tweak_ids.add(tid)
        for domain, key, typ, _value in tweak["writes"]:
            if typ not in DEFAULT_TYPES:
                raise CatalogError(f"{tid}: bad type {typ!r} for {domain} {key}")


@lru_cache
def load_all() -> tuple[list[dict], dict, list[dict]]:
    apps = _load("catalog.json")
    questionnaire = _load("questionnaire.json")
    tweaks = _load("macos_defaults.json")
    validate(apps, questionnaire, tweaks)
    return apps, questionnaire, tweaks


def apps_by_id() -> dict[str, dict]:
    return {a["id"]: a for a in load_all()[0]}


def tweaks_by_id() -> dict[str, dict]:
    return {t["id"]: t for t in load_all()[2]}
