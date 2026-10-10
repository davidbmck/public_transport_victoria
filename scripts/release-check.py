"""Networked HACS installation and PTV checks with fresh disposable HA storage.

Credentials arrive on stdin and exist only in memory/container tmpfs. This is
separate from the offline mocked regression suite and never loads production HA.
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from types import MappingProxyType

DOMAIN = "public_transport_victoria"
UPSTREAM = "bremor/public_transport_victoria"
FORK = "davidbmck/public_transport_victoria"
HACS_VERSION = "2.0.5"
HACS_SHA256 = "97be6b824a4f38e683728cc6dd72367f6b8bad0a43428b1b3b987a3087adf413"
EXPECTED_VERSION = "0.8.0"


class PrivateLogs(logging.Handler):
    """Keep full logs in memory for checks, never print raw transport exceptions."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))

    def check(self, credentials):
        captured = "\n".join(self.records)
        sensitive = [credentials.get("github_token"), "signature=", "devid="]
        if credentials.get("ptv"):
            sensitive.append(credentials["ptv"]["api_key"])
        assert all(not value or value not in captured for value in sensitive), (
            "Sensitive data found in captured logs (contents withheld)"
        )


async def home_assistant(config_dir):
    from homeassistant import config_entries, loader
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import frame
    from homeassistant.setup import async_setup_component

    hass = HomeAssistant(str(config_dir))
    loader.async_setup(hass)
    frame.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    assert await async_setup_component(hass, "network", {})
    await hass.config.async_set_time_zone("Australia/Melbourne")
    return hass


async def install(hass, repository_name, ref, token):
    """Use the actual HACS registration/download backend used by its UI."""
    from aiogithubapi import GitHub, GitHubAPI
    from awesomeversion import AwesomeVersion
    from custom_components.hacs.base import HacsBase
    from custom_components.hacs.enums import HacsCategory
    from homeassistant.const import __version__ as ha_version
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.loader import async_get_custom_components

    hacs = HacsBase()
    hacs.hass = hass
    hacs.session = async_get_clientsession(hass)
    hacs.core.config_path = hass.config.path()
    hacs.core.ha_version = AwesomeVersion(ha_version)
    hacs.version = AwesomeVersion(HACS_VERSION)
    hacs.github = GitHub(token, hacs.session)
    hacs.githubapi = GitHubAPI(token=token, session=hacs.session)
    await async_get_custom_components(hass)
    errors = await hacs.async_register_repository(
        repository_name, HacsCategory.INTEGRATION, ref=ref
    )
    assert not errors, "HACS custom repository registration failed"
    repository = hacs.repositories.get_by_full_name(repository_name)
    assert repository is not None
    await repository.async_download_repository(ref=ref)
    assert repository.data.installed
    assert not repository.validate.errors
    target = Path(hass.config.path("custom_components", DOMAIN))

    def inspect_installation():
        assert target.is_dir() and (target / "sensor.py").is_file()
        assert not list(target.rglob("*.pyc"))
        assert not list(target.rglob("*.js"))
        return json.loads((target / "manifest.json").read_text())["version"]

    return await hass.async_add_executor_job(inspect_installation)


async def ptv_request(hass, credentials, path):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.public_transport_victoria.PublicTransportVictoria import (
        public_transport_victoria as ptv_client,
    )

    session = async_get_clientsession(hass)
    url = ptv_client.build_URL(credentials["id"], credentials["api_key"], path)
    async with session.get(
        url, timeout=__import__("aiohttp").ClientTimeout(total=30)
    ) as response:
        response.raise_for_status()
        return await response.json()


async def add_existing_entries(hass, credentials):
    """Create fresh version-1 entries using only public API discovery data."""
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState

    routes = (await ptv_request(hass, credentials, "/v3/routes?route_types=0"))[
        "routes"
    ]
    route = routes[0]
    route_id = route["route_id"]
    directions = (
        await ptv_request(hass, credentials, f"/v3/directions/route/{route_id}")
    )["directions"]
    stops = (
        await ptv_request(hass, credentials, f"/v3/stops/route/{route_id}/route_type/0")
    )["stops"]
    assert len(directions) >= 2 and stops
    for direction in directions[:2]:
        data = {
            "id": credentials["id"],
            "api_key": credentials["api_key"],
            "route_type": "0",
            "route_type_name": "Train",
            "route": str(route_id),
            "route_name": route["route_name"],
            "direction": str(direction["direction_id"]),
            "direction_name": direction["direction_name"],
            "stop": str(stops[0]["stop_id"]),
            "stop_name": stops[0]["stop_name"],
        }
        entry = ConfigEntry(
            domain=DOMAIN,
            version=1,
            minor_version=1,
            data=data,
            options={},
            source="user",
            title="Disposable PTV installation check",
            unique_id=None,
            discovery_keys=MappingProxyType({}),
            subentries_data=[],
        )
        await hass.config_entries.async_add(entry)
        assert entry.state is ConfigEntryState.LOADED
    await hass.async_block_till_done()


def identities(hass):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return {
        entity.entity_id: [entity.unique_id, entity.config_entry_id, entity.name]
        for entity in registry.entities.values()
        if entity.platform == DOMAIN and not entity.unique_id.endswith("_route_alerts")
    }


async def verify_live(hass, credentials):
    """Bounded representative route requests; no saved responses or signed URLs."""
    from custom_components.public_transport_victoria.PublicTransportVictoria import (
        public_transport_victoria as ptv_client,
    )

    types = (await ptv_request(hass, credentials, "/v3/route_types"))["route_types"]
    samples = []
    for mode in (0, 1, 2, 3):
        assert any(item["route_type"] == mode for item in types)
        routes = (
            await ptv_request(hass, credentials, f"/v3/routes?route_types={mode}")
        )["routes"]
        assert routes
        connector = ptv_client.Connector(
            hass, credentials["id"], credentials["api_key"], route=routes[0]["route_id"]
        )
        notices = await connector.async_route_disruptions()
        samples.append(
            {
                "route_type": mode,
                "route_id": int(connector.route),
                "notice_count": len(notices),
                "categories": sorted(
                    {
                        category
                        for notice in notices
                        for category in notice["categories"]
                    }
                ),
            }
        )
    entries = hass.config_entries.async_entries(DOMAIN)
    coordinators = [
        hass.data[DOMAIN][entry.entry_id].alert_coordinator for entry in entries
    ]
    assert len(coordinators) == 2 and coordinators[0] is coordinators[1]
    coordinator = coordinators[0]
    assert coordinator.last_update_success
    timestamp, notices = coordinator.last_successful_update, coordinator.data
    key = coordinator._connector.api_key
    coordinator._connector.api_key = "synthetic-invalid-key-for-release-check"
    try:
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert not coordinator.last_update_success
        assert coordinator.data == notices
        assert coordinator.last_successful_update == timestamp
    finally:
        coordinator._connector.api_key = key
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    for entry in entries:
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return samples


async def phase(name, config_dir, ref, credentials):
    os.chdir(config_dir)
    hass = await home_assistant(config_dir)
    try:
        if name.startswith("install"):
            repository = UPSTREAM if name == "install-baseline" else FORK
            target_ref = "master" if name == "install-baseline" else ref
            version = await install(
                hass, repository, target_ref, credentials.get("github_token")
            )
            assert version == (
                "0.7" if name == "install-baseline" else EXPECTED_VERSION
            )
            print(json.dumps({"phase": name, "installed_version": version}))
            return
        if name == "baseline":
            await add_existing_entries(hass, credentials["ptv"])
            before = identities(hass)
            assert len(before) == 10
            (config_dir / "departure-identities.json").write_text(json.dumps(before))
        else:
            for entry in hass.config_entries.async_entries(DOMAIN):
                assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            before = json.loads((config_dir / "departure-identities.json").read_text())
            assert identities(hass) == before, (
                "Departure registry identities changed on upgrade"
            )
            from homeassistant.loader import async_get_integration

            integration = await async_get_integration(hass, DOMAIN)
            assert integration.version == EXPECTED_VERSION
            assert str(integration.file_path).startswith(str(config_dir))
            samples = await verify_live(hass, credentials["ptv"])
            assert identities(hass) == before
            print(
                json.dumps(
                    {
                        "phase": name,
                        "departure_identities": len(before),
                        "samples": samples,
                    }
                )
            )
    finally:
        await hass.async_stop(force=True)


async def download_hacs(config_dir):
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://github.com/hacs/integration/releases/download/{HACS_VERSION}/hacs.zip",
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            response.raise_for_status()
            archive = await response.read()
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != HACS_SHA256:
        print(
            json.dumps({"hacs_archive_sha256": actual_digest, "bytes": len(archive)}),
            flush=True,
        )
        raise AssertionError("HACS archive digest mismatch")
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        bundle.extractall(config_dir / "custom_components" / "hacs")


def main():
    credentials = json.load(sys.stdin)
    logs = PrivateLogs()
    logging.basicConfig(level=logging.DEBUG, handlers=[logs], force=True)
    if len(sys.argv) == 4:
        asyncio.run(phase(sys.argv[1], Path(sys.argv[2]), sys.argv[3], credentials))
        logs.check(credentials)
        return
    ref = sys.argv[1]
    install_only = credentials.get("install_only", False)
    assert install_only or credentials.get("ptv"), (
        "Live PTV credentials are required for release acceptance"
    )
    with tempfile.TemporaryDirectory(prefix="ptv-release-") as directory:
        config_dir = Path(directory)
        asyncio.run(download_hacs(config_dir))
        phases = (
            ("install-baseline", "install-candidate")
            if install_only
            else ("install-baseline", "baseline", "install-candidate", "candidate")
        )
        for name in phases:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    name,
                    str(config_dir),
                    ref,
                ],
                input=json.dumps(credentials),
                text=True,
                capture_output=True,
                timeout=300,
            )
            if result.returncode:
                print(result.stdout, end="", flush=True)
                for line in result.stderr.splitlines():
                    if line.startswith("Release check failed ("):
                        print(line, file=sys.stderr)
                raise RuntimeError(
                    f"Disposable release phase failed: {name} (raw logs withheld)"
                )
            print(result.stdout, end="", flush=True)
        logs.check(credentials)
        if install_only:
            print(json.dumps({"live_api": "not_run", "upgrade_identities": "not_run"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        location = traceback.extract_tb(error.__traceback__)[-1]
        print(
            f"Release check failed ({type(error).__name__}) at "
            f"{'/'.join(Path(location.filename).parts[-3:])}:{location.lineno}; "
            "raw text withheld.",
            file=sys.stderr,
        )
        sys.exit(1)
