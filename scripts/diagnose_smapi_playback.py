#!/usr/bin/env python3
"""Capture privacy-redacted Sonos playback and network telemetry.

The probe is read-only. It neither starts playback nor changes the queue. During
an interactive capture, press Enter whenever an audible dropout occurs; the
marker is written beside the surrounding device counters in the JSONL output.
"""

from __future__ import annotations

import argparse
import json
import re
import select
import socket
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SSDP_TARGET = ("239.255.255.250", 1900)
ZONE_PLAYER = "urn:schemas-upnp-org:device:ZonePlayer:1"
AVT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
USER_AGENT = "sonos-firmware-archive/smapi-diagnostic"


def discover_players(timeout: float = 3.0) -> list[dict[str, str]]:
    message = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {ZONE_PLAYER}\r\n\r\n"
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(0.25)
    sock.sendto(message, SSDP_TARGET)
    locations: set[str] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload, _ = sock.recvfrom(65535)
        except TimeoutError:
            continue
        match = re.search(r"(?im)^location:\s*(\S+)", payload.decode("latin1", "replace"))
        if match:
            locations.add(match.group(1))

    players = []
    for location in sorted(locations):
        try:
            root = ET.fromstring(fetch(location, timeout=2.0))
            host_match = re.match(r"https?://([^:/]+)", location)
            if host_match is None:
                continue
            values = {element.tag.rsplit("}", 1)[-1]: element.text or "" for element in root.iter()}
            players.append(
                {
                    "host": host_match.group(1),
                    "room": values.get("roomName", ""),
                    "model": values.get("modelName", ""),
                    "firmware": values.get("softwareVersion", ""),
                }
            )
        except Exception:
            continue
    return players


def fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 4.0) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def soap(host: str, action: str) -> dict[str, str]:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<s:Body><u:{action} xmlns:u="{AVT_SERVICE}"><InstanceID>0</InstanceID>'
        f"</u:{action}></s:Body></s:Envelope>"
    ).encode()
    root = ET.fromstring(
        fetch(
            f"http://{host}:1400/MediaRenderer/AVTransport/Control",
            data=body,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": f'"{AVT_SERVICE}#{action}"',
            },
        )
    )
    return {element.tag.rsplit("}", 1)[-1]: element.text or "" for element in root.iter()}


def parse_clock(value: str) -> int | None:
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":"))
        return hours * 3600 + minutes * 60 + seconds
    except (TypeError, ValueError):
        return None


def classify_source(uri: str) -> str:
    if re.search(r"(?:[?&]|%26)sid(?:=|%3d)204(?:[&%]|$)", uri, re.IGNORECASE):
        return "apple_music_smapi"
    if uri.startswith("x-sonos-http:"):
        return "other_smapi_http"
    if uri.startswith("x-rincon:"):
        return "group_member"
    return (uri.split(":", 1)[0] or "none").lower()


def parse_network_status(payload: str) -> dict[str, int | list[int] | None]:
    phy = re.search(r"PHY errors since last reading/reset:\s*(\d+)", payload)
    noise = [int(value) for value in re.findall(r"Noise Floor:\s*(-?\d+) dBm", payload)]
    channel = re.search(r"IEEE channel:\s*(\d+)", payload)
    ani = re.search(r"OFDM ANI level:\s*(\d+)", payload)
    return {
        "phy_errors": int(phy.group(1)) if phy else None,
        "noise_floor_dbm": noise,
        "channel": int(channel.group(1)) if channel else None,
        "ani_level": int(ani.group(1)) if ani else None,
    }


def parse_ifconfig(payload: str) -> dict[str, int | None]:
    match = re.search(
        r"br0\s+.*?RX packets:(\d+) errors:(\d+) dropped:(\d+).*?"
        r"TX packets:(\d+) errors:(\d+) dropped:(\d+)",
        payload,
        re.DOTALL,
    )
    if match is None:
        return {key: None for key in ("rx_packets", "rx_errors", "rx_dropped", "tx_packets", "tx_errors", "tx_dropped")}
    keys = ("rx_packets", "rx_errors", "rx_dropped", "tx_packets", "tx_errors", "tx_dropped")
    return dict(zip(keys, map(int, match.groups()), strict=True))


def status_page(host: str, path: str) -> str:
    return fetch(f"http://{host}:1400{path}").decode("utf-8", "replace")


def sample(host: str, firmware: str) -> dict:
    started = time.monotonic()
    transport = soap(host, "GetTransportInfo")
    position = soap(host, "GetPositionInfo")
    uri = position.get("TrackURI", "")
    network = parse_network_status(status_page(host, "/status/proc/ath_rincon/status"))
    interface = parse_ifconfig(status_page(host, "/status/ifconfig"))
    return {
        "type": "sample",
        "time": datetime.now(timezone.utc).isoformat(),
        "monotonic": round(time.monotonic(), 3),
        "probe_ms": round((time.monotonic() - started) * 1000, 1),
        "firmware": firmware,
        "transport_state": transport.get("CurrentTransportState"),
        "transport_status": transport.get("CurrentTransportStatus"),
        "track_number": int(position.get("Track", "0") or 0),
        "position_seconds": parse_clock(position.get("RelTime", "")),
        "duration_seconds": parse_clock(position.get("TrackDuration", "")),
        "source": classify_source(uri),
        "network": network,
        "interface": interface,
    }


def automatic_events(previous: dict | None, current: dict) -> list[dict]:
    if previous is None:
        return []
    events = []
    if current["transport_status"] != "OK":
        events.append({"kind": "transport_error", "value": current["transport_status"]})
    old_pos, new_pos = previous.get("position_seconds"), current.get("position_seconds")
    if (
        previous.get("transport_state") == current.get("transport_state") == "PLAYING"
        and old_pos is not None
        and new_pos is not None
        and current["track_number"] == previous["track_number"]
        and new_pos <= old_pos
    ):
        events.append({"kind": "timeline_stall", "seconds": old_pos})
    if (
        current["track_number"] != previous["track_number"]
        and previous.get("duration_seconds")
        and old_pos is not None
        and old_pos + 5 < previous["duration_seconds"]
    ):
        events.append(
            {
                "kind": "premature_track_change",
                "previous_position_seconds": old_pos,
                "previous_duration_seconds": previous["duration_seconds"],
            }
        )
    return events


def write_record(handle, record: dict) -> None:
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", required=True, help="Sonos room name to observe")
    parser.add_argument("--output", type=Path, required=True, help="privacy-redacted JSONL capture")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=0.0, help="seconds; zero runs until Ctrl-C")
    parser.add_argument("--non-interactive", action="store_true", help="disable Enter-key dropout markers")
    args = parser.parse_args()
    if args.interval < 0.25:
        parser.error("--interval must be at least 0.25 seconds")

    matches = [player for player in discover_players() if player["room"].casefold() == args.room.casefold()]
    if len(matches) != 1:
        print(f"error: expected one room named {args.room!r}, found {len(matches)}", file=sys.stderr)
        return 2
    player = matches[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Observing {args.room} ({player['model']}, firmware {player['firmware']}).")
    print("This is read-only: playback and the queue will not be changed.")
    if not args.non_interactive:
        print("Press Enter immediately after each audible dropout; Ctrl-C stops the capture.")

    started = time.monotonic()
    previous = None
    failures = 0
    samples = 0
    marker_input_open = not args.non_interactive
    with args.output.open("w", encoding="utf-8") as handle:
        write_record(
            handle,
            {
                "type": "capture_start",
                "time": datetime.now(timezone.utc).isoformat(),
                "room": "selected-room",
                "model": player["model"],
                "firmware": player["firmware"],
                "privacy": "No IP, player ID, household ID, URI, metadata, title, or account token is recorded.",
            },
        )
        try:
            while not args.duration or time.monotonic() - started < args.duration:
                loop_started = time.monotonic()
                if marker_input_open and select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline()
                    if line:
                        write_record(
                            handle,
                            {
                                "type": "event",
                                "time": datetime.now(timezone.utc).isoformat(),
                                "monotonic": round(time.monotonic(), 3),
                                "kind": "audible_dropout",
                            },
                        )
                        print("Marked audible dropout.")
                    else:
                        marker_input_open = False
                try:
                    current = sample(player["host"], player["firmware"])
                    write_record(handle, current)
                    samples += 1
                    for event in automatic_events(previous, current):
                        write_record(handle, {"type": "event", "time": current["time"], **event})
                    previous = current
                except Exception as error:
                    failures += 1
                    write_record(
                        handle,
                        {
                            "type": "probe_failure",
                            "time": datetime.now(timezone.utc).isoformat(),
                            "error_type": type(error).__name__,
                        },
                    )
                time.sleep(max(0.0, args.interval - (time.monotonic() - loop_started)))
        except KeyboardInterrupt:
            pass
        write_record(
            handle,
            {
                "type": "capture_end",
                "time": datetime.now(timezone.utc).isoformat(),
                "samples": samples,
                "probe_failures": failures,
            },
        )
    print(f"Capture saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
