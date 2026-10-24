"""Automated quality-checklist verification.

Locks the Home Assistant quality checklist items that are verifiable from
the repository: translation coverage (entity names, icons, exceptions),
services.yaml completeness, unique ids, has_entity_name, PARALLEL_UPDATES,
manifest keys and EN/FR parity.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from custom_components.rts_gateway import config_flow, const
from custom_components.rts_gateway.const import DOMAIN

REPO_ROOT = Path(__file__).parent.parent
COMPONENT_DIR = "custom_components/rts_gateway"


def _load(path):
    with open(f"{COMPONENT_DIR}/{path}", encoding="utf-8") as fh:
        return json.load(fh)


def _load_yaml(path):
    with open(f"{COMPONENT_DIR}/{path}", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _module_source(module_name):
    with open(f"{COMPONENT_DIR}/{module_name}.py", encoding="utf-8") as fh:
        return fh.read()


def _entity_platforms():
    for platform in ("cover", "switch", "light", "button", "binary_sensor"):
        yield platform, _module_source(platform)


def _leaf_keys(tree, prefix=""):
    out = set()
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _leaf_keys(value, path)
        else:
            out.add(path)
    return out


@pytest.fixture(name="strings")
def strings_fixture():
    return _load("strings.json")


@pytest.fixture(name="fr")
def fr_fixture():
    return _load("translations/fr.json")


def test_en_fr_parity(strings, fr):
    """EN/FR: same structure, same keys (docs-*, translations)."""
    assert _leaf_keys(strings) == _leaf_keys(fr)


def test_translation_keys_have_entity_names(strings, fr):
    """entity-translations: every translation_key has a name in EN+FR."""
    for platform, source in _entity_platforms():
        used = set(re.findall(r'_attr_translation_key\s*=\s*"(\w+)"', source))
        used |= set(
            re.findall(r'self\._attr_translation_key = "(\w+)"', source)
        )
        if platform == "binary_sensor":
            used |= {"wind", "sun"}  # kind-driven
        for key in used:
            assert strings["entity"][platform][key]["name"], (platform, key)
            assert fr["entity"][platform][key]["name"], (platform, key)


def test_icons_come_from_translations(strings, fr):
    """icon-translations: translated entities carry their icon in strings.

    Entities with a translation_key must not set _attr_icon: the icon
    lives in entity.<platform>.<key>.icon. Config-driven icons (per
    device type, from DEVICE_TYPE_MAPPING) are out of scope.
    """
    for platform, source in _entity_platforms():
        used = set(re.findall(r'_attr_translation_key\s*=\s*"(\w+)"', source))
        used |= set(
            re.findall(r'self\._attr_translation_key = "(\w+)"', source)
        )
        if platform == "binary_sensor":
            used |= {"wind", "sun"}
        for key in used:
            assert "icon" in strings["entity"][platform][key], (
                platform,
                key,
            )
            assert "icon" in fr["entity"][platform][key], (platform, key)
        # Les classes portant une cle de traduction ne fixent pas d'icone.
        assert "_attr_icon" not in source or platform in ("cover", "switch", "light"), platform


def test_exception_keys_exist(strings, fr):
    """exception-translations: every translation_key raised exists."""
    raised = set()
    for module in (
        "services",
        "config_flow",
        "cover",
        "switch",
        "light",
        "button",
    ):
        source = _module_source(module)
        raised |= set(re.findall(r'translation_key="(\w+)"', source))
    for key in raised:
        in_exceptions = key in strings.get("exceptions", {})
        in_config = key in strings["config"].get("error", {})
        in_options = key in strings["options"].get("error", {})
        assert in_exceptions or in_config or in_options, key
        fr_ok = (
            key in fr.get("exceptions", {})
            or key in fr["config"].get("error", {})
            or key in fr["options"].get("error", {})
        )
        assert fr_ok, key


def test_services_yaml_matches_registered_services():
    """docs-actions: services.yaml lists exactly the registered services."""
    services = _load_yaml("services.yaml")
    source = _module_source("services")
    actually_registered = set(re.findall(r"DOMAIN, (SERVICE_\w+),", source))
    expected = {getattr(const, sym) for sym in actually_registered}
    assert set(services) == expected


def test_every_entity_has_unique_id_and_has_entity_name():
    """entity-unique-id + has-entity-name on every platform."""
    for platform, source in _entity_platforms():
        assert "unique_id" in source, platform
        assert "_attr_has_entity_name = True" in source, platform


def test_parallel_updates_on_every_platform():
    """parallel-updates: every entity platform sets it."""
    for platform, source in _entity_platforms():
        assert "PARALLEL_UPDATES" in source, platform


def test_manifest_quality_keys():
    """dependency-transparency / integration-owner / diagnostics."""
    manifest = _load("manifest.json")
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["diagnostics"] is True
    assert manifest["codeowners"], "integration-owner"
    assert "radio_frequency" in manifest["dependencies"]
    assert manifest["iot_class"]
    # HACS publishing requirements (hacs.xyz/docs/publish/integration).
    for key in ("documentation", "issue_tracker", "name", "version"):
        assert manifest.get(key), key
    assert not manifest["version"].startswith("v")
    for url in (manifest["documentation"], manifest["issue_tracker"]):
        assert url.startswith("https://"), url


def test_hacs_brand_assets():
    """HACS listing icons: a brand directory with an icon.png."""
    assert (REPO_ROOT / "brand" / "icon.png").is_file()
    assert (REPO_ROOT / "brand" / "icon@2x.png").is_file()


def test_data_descriptions_match_field_names(strings, fr):
    """config-flow data_description: keys must match existing fields."""
    for lang in (strings, fr):
        for section in ("config", "options"):
            for step in lang[section].get("step", {}).values():
                fields = set(step.get("data", {}))
                for desc_key in step.get("data_description", {}):
                    assert desc_key in fields, desc_key
                for form_section in step.get("sections", {}).values():
                    fields = set(form_section.get("data", {}))
                    for desc_key in form_section.get("data_description", {}):
                        assert desc_key in fields, desc_key


def test_device_option_fields_translated(strings, fr):
    """device_options: a lone section renders flat, so its field
    labels must also exist at step level (not only in sections)."""
    fields = set()
    for device_type in const.SomfyDeviceType:
        for protocol in const.SomfyProtocol:
            keys = config_flow._device_option_fields(
                {
                    const.CONF_DEVICE_TYPE: device_type.value,
                    const.CONF_PROTOCOL: protocol.value,
                }
            )
            fields |= {key.schema for key in keys}
    for lang in (strings, fr):
        for flow in ("config", "options"):
            step = lang[flow]["step"]["device_options"]
            missing = fields - set(step["data"])
            assert not missing, (flow, sorted(missing))


def test_no_hardcoded_french_entity_names():
    """entity-translations: no French literals left in entity code."""
    for platform, source in _entity_platforms():
        for line in source.split("\n"):
            if "return" in line and any(
                word in line
                for word in ("Recalage", "Appairer", "Mémoriser", "programmation")
            ):
                pytest.fail(
                    f"hardcoded French name: {platform}: {line.strip()}"
                )


# =============================================================================
# quality_scale.yaml
# =============================================================================
QUALITY_SCALE_RULES = frozenset({
    # Bronze
    "action-setup", "appropriate-polling", "brands", "common-modules",
    "config-flow", "config-flow-test-coverage", "dependency-transparency",
    "docs-actions", "docs-conditions", "docs-high-level-description",
    "docs-installation-instructions", "docs-removal-instructions",
    "docs-triggers", "entity-event-setup", "entity-unique-id",
    "has-entity-name", "runtime-data", "test-before-configure",
    "test-before-setup", "unique-config-entry",
    # Silver
    "action-exceptions", "config-entry-unloading",
    "docs-configuration-parameters", "docs-installation-parameters",
    "entity-unavailable", "integration-owner", "log-when-unavailable",
    "parallel-updates", "reauthentication-flow", "test-coverage",
    # Gold
    "devices", "diagnostics", "discovery", "discovery-update-info",
    "docs-data-update", "docs-examples", "docs-known-limitations",
    "docs-supported-devices", "docs-supported-functions",
    "docs-troubleshooting", "docs-use-cases", "dynamic-devices",
    "entity-category", "entity-device-class", "entity-disabled-by-default",
    "entity-translations", "exception-translations", "icon-translations",
    "reconfiguration-flow", "repair-issues", "stale-devices",
    # Platinum
    "async-dependency", "inject-websession", "strict-typing",
})


EXEMPT_RULES = frozenset({
    "docs-conditions", "reauthentication-flow", "discovery",
    "discovery-update-info", "inject-websession",
})


def test_quality_scale_yaml():
    """quality_scale.yaml: checklist markdown verrouillee."""
    with open(f"{COMPONENT_DIR}/quality_scale.yaml", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    found = set()
    for line in lines:
        match = re.match(r"- \[( |x)\] `(\w[-\w]*)`", line)
        if not match:
            continue
        checked, rule = match.groups()
        assert rule in QUALITY_SCALE_RULES, rule
        found.add(rule)
        # Chaque regle pointe vers le code qui l'implemente.
        assert "](" in line, rule
        if rule in EXEMPT_RULES:
            assert "exempt" in line, rule
        elif checked == " ":
            assert rule in {"brands", "repair-issues"}, rule
    assert found == QUALITY_SCALE_RULES, QUALITY_SCALE_RULES - found
