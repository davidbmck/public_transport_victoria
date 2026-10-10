"""Validate the actual user-guide templates and automation with synthetic HA data."""

import html
import re
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml
from homeassistant.helpers.template import Template
from homeassistant.setup import async_setup_component

GUIDE = (Path(__file__).parent.parent / "docs/alerts.md").read_text()
EXAMPLES = [
    yaml.safe_load(block) for block in re.findall(r"```yaml\n(.*?)\n```", GUIDE, re.S)
]
CARDS = [example for example in EXAMPLES if "content" in example]
AUTOMATION = next(example for example in EXAMPLES if "actions" in example)
FILTER = re.search(r"```jinja\n(.*?)\n```", GUIDE, re.S).group(1)
ENTITY = "sensor.example_route_alerts"
STALE = [{"title": "Old cached notice", "description": "Old data"}]


class Tags(HTMLParser):
    """Collect parsed tags/attributes, distinguishing text from injected markup."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(name for name, _ in attrs)


@pytest.mark.parametrize("card", CARDS, ids=lambda card: card["type"])
@pytest.mark.parametrize(
    ("state", "attributes", "expected"),
    [
        (None, {}, "Route alert feed unavailable."),
        ("unknown", {}, "Route alert feed unavailable."),
        ("unavailable", {"alerts": STALE}, "Route alert feed unavailable."),
        ("0", {"alerts": []}, "No route notices. Feed is available."),
        ("2", {}, "Notice details are not available."),
        ("2", {"alerts": None}, "Notice details are not available."),
        ("2", {"alerts": "missing list"}, "Notice details are not available."),
        ("2", {"alerts": {}}, "Notice details are not available."),
    ],
)
async def test_card_feed_states_and_missing_attributes(
    hass, card, state, attributes, expected
):
    if state is not None:
        hass.states.async_set(ENTITY, state, attributes)
    rendered = Template(card["content"], hass).async_render(parse_result=False)
    assert expected in rendered
    assert "Old cached notice" not in rendered
    if state in (None, "unknown", "unavailable"):
        assert "No route notices" not in rendered


@pytest.mark.parametrize("card", CARDS, ids=lambda card: card["type"])
async def test_card_preserves_text_without_inserting_external_markup(hass, card):
    attack = (
        '<script>alert("synthetic")</script><img src=x onerror="synthetic"> & <svg>'
    )
    hass.states.async_set(
        ENTITY,
        "1",
        {
            "alerts": [
                {
                    "title": attack,
                    "description": attack,
                    "disruption_status": attack,
                    "url": "javascript:synthetic",
                }
            ]
        },
    )
    rendered = Template(card["content"], hass).async_render(parse_result=False)
    parsed = Tags()
    parsed.feed(rendered)
    assert not {"script", "img", "svg", "a"} & set(parsed.tags)
    assert not any(name.startswith("on") for name in parsed.attributes)
    assert html.unescape(rendered).count(attack) == 3
    assert "javascript:synthetic" not in rendered


@pytest.mark.parametrize("card", CARDS, ids=lambda card: card["type"])
async def test_card_displays_current_planned_and_missing_text(hass, card):
    hass.states.async_set(
        ENTITY,
        "3",
        {
            "alerts": [
                {
                    "title": "Current notice",
                    "description": "Current detail",
                    "disruption_status": "Current",
                },
                {
                    "title": "Planned notice",
                    "description": "Future detail",
                    "disruption_status": "Planned",
                },
                {"title": None, "description": None, "disruption_status": None},
            ]
        },
    )
    rendered = Template(card["content"], hass).async_render(parse_result=False)
    for text in (
        "Current notice",
        "Current detail",
        "Planned notice",
        "Future detail",
        "Untitled notice",
        "No description supplied",
        "Unknown",
    ):
        assert text in rendered


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "0", "2"])
async def test_automation_configuration_and_feed_status_actions(hass, tmp_path, state):
    hass.config.config_dir = str(tmp_path)
    calls = []

    async def notify(call):
        calls.append(dict(call.data))

    hass.services.async_register("persistent_notification", "create", notify)
    if state is not None:
        hass.states.async_set(ENTITY, state)
    assert await async_setup_component(
        hass, "automation", {"automation": [deepcopy(AUTOMATION)]}
    )
    await hass.async_block_till_done()
    entity_id = next(iter(hass.states.async_entity_ids("automation")))
    await hass.services.async_call(
        "automation", "trigger", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0]["notification_id"] == "ptv_route_alerts_example"
    if state == "0":
        assert calls[0]["title"] == "No route notices"
        assert calls[0]["message"] == "No route notices. Feed is available."
    elif state == "2":
        assert calls[0]["title"] == "Route notices"
        assert calls[0]["message"].startswith(
            "2 route notices, including planned notices."
        )
    else:
        assert calls[0]["title"] == "Route alert feed unavailable"
        assert "does not mean there are no notices" in calls[0]["message"]


async def test_automation_state_trigger_handles_failure_recovery_and_empty_feed(
    hass, tmp_path
):
    hass.config.config_dir = str(tmp_path)
    messages = []

    async def notify(call):
        messages.append(call.data["title"])

    hass.services.async_register("persistent_notification", "create", notify)
    hass.states.async_set(ENTITY, "0", {"alerts": []})
    assert await async_setup_component(
        hass, "automation", {"automation": [deepcopy(AUTOMATION)]}
    )
    await hass.async_block_till_done()
    for state, title in [
        ("2", "Route notices"),
        ("unavailable", "Route alert feed unavailable"),
        ("0", "No route notices"),
    ]:
        hass.states.async_set(ENTITY, state)
        await hass.async_block_till_done()
        assert messages[-1] == title
    count = len(messages)
    hass.states.async_set(
        ENTITY, "0", {"alerts": [], "last_successful_update": "synthetic"}
    )
    await hass.async_block_till_done()
    assert len(messages) == count  # to: null ignores attribute-only updates.


async def test_optional_status_filter_and_unavailable_guard(hass):
    notices = [
        {"disruption_id": 1, "disruption_status": "Current"},
        {"disruption_id": 2, "disruption_status": "Planned"},
        {"disruption_id": 3, "disruption_status": None},
        {"disruption_id": 4},
        {"disruption_id": 5, "disruption_status": "Unfamiliar"},
    ]
    hass.states.async_set(ENTITY, "5", {"alerts": notices})
    selected = Template(FILTER, hass).async_render()
    assert [notice["disruption_id"] for notice in selected] == [1, 2, 3, 4]
    unfiltered = FILTER.replace("['Current', 'Planned']", "[]")
    assert Template(unfiltered, hass).async_render() == notices
    hass.states.async_set(ENTITY, "unavailable", {"alerts": notices})
    assert (
        Template(FILTER, hass).async_render() == "Feed unavailable or details missing"
    )


@pytest.mark.parametrize("attributes", [{}, {"alerts": None}, {"alerts": "bad"}])
async def test_optional_filter_guards_missing_or_invalid_details(hass, attributes):
    hass.states.async_set(ENTITY, "2", attributes)
    assert (
        Template(FILTER, hass).async_render() == "Feed unavailable or details missing"
    )
