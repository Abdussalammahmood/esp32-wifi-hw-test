---
name: execute-wifi-hw-test
description: |
  Run the ESP32 WiFi hardware verification harness (esp32-wifi-hw-test) against a device under
  test and a known-good reference board, then judge whether the DUT's WiFi hardware is correct.
  Covers: finding COM ports, building/flashing the vendored wifi/iperf firmware on both boards,
  writing the run config, executing wifi_hw_test.py, reading the generated markdown report, and
  the failure playbook (stale NVS, busy port, supplicant panics, DTR/RTS traps). Falls back to
  docs/MANUAL_TESTING.md when the user wants to run the test by hand.
---

# Execute the WiFi hardware test

Use when someone asks *"is the WiFi hardware on this board OK?"*, *"check the radio/antenna"*,
*"verify WiFi before certification"*, or reports WiFi problems on a custom/first-article board.

The harness compares a **DUT** against a **known-good reference board** in the same room at the same
moment and writes a verdict report. It never flashes your product firmware.

## Inputs to establish first

| Need | How |
|---|---|
| DUT port + board name + chip | `python wifi_hw_test.py --list-ports`, ask the user, or read the ROM banner |
| Reference port + board name + chip | same |
| Firmware already on both boards? | ask; the firmware **source is in this repo** (`firmware/iperf-esp32s3/`, `firmware/iperf-esp32c6/`) and both build offline |
| **ESP-IDF version** | must be **v5.5.4** (`idf.py --version`) — the version the vendored firmware and the reference results were built with; check before building |
| IDF location | e.g. `D:\IDF_...\esp-idf`, tools root, venv python |

Do **not** guess ports. Two boards on the wrong ports produce a confident but meaningless report.

**Wrong IDF version is the most common self-inflicted failure**: building with e.g. v5.4.1 rewrites
`firmware/*/dependencies.lock` (`version: 5.5.4` → `5.4.1`) and changes Wi-Fi behaviour, so the numbers
stop being comparable. Fix: `idf.py fullclean`, delete `sdkconfig`, `git checkout -- dependencies.lock`,
then rebuild with v5.5.4.

## Two ways the user can run this

- **You do it (default):** follow the steps below, run `wifi_hw_test.py`, then report the verdicts.
- **The user does it by hand:** hand them [`docs/MANUAL_TESTING.md`](../../../docs/MANUAL_TESTING.md)
  (repo root → `docs/MANUAL_TESTING.md`) — the same test as two serial terminals plus the acceptance
  table. Offer this whenever the user says they want to build/flash/measure themselves, or when they
  don't want an agent driving their boards.

## Steps

1. **List ports**
   ```bash
   python wifi_hw_test.py --list-ports
   ```
2. **Firmware on both boards** (skip if already flashed with the iperf example). The firmware is
   vendored in this repo and builds offline:
   ```bash
   cd firmware/iperf-esp32s3 && idf.py set-target esp32s3 && idf.py -p <DUT_PORT> flash && cd ../..
   cd firmware/iperf-esp32c6 && idf.py set-target esp32c6 && idf.py -p <REF_PORT> flash && cd ../..
   ```
   Different chip? Generate a copy:
   ```bash
   python tools/prepare_firmware.py --target <chip> --dest firmware/iperf-<chip> \
       --idf-path <IDF> --tools-path <TOOLS> --python-env <VENV> --path-prepend <TOOL_BINS> --build
   ```
   The example provides the console commands the harness uses (`scan`, `sta_connect`, `wifi_mode`,
   `ap_set`, `ping`, `iperf`).
3. **Config** — copy `config/astra_s3_vs_c6.json` → `config/<your-run>.json` and set ports, names,
   chips, durations, `reference_baseline`. Keep `durations.udp_bitrate` at **40** (see the UDP rule
   below); `udp_bitrates: [20, 30, 40]` adds the offered-load sweep.
4. **Run**
   ```bash
   python wifi_hw_test.py --config config/<your-run>.json --out results/
   ```
   Add `--erase-nvs` if association hangs. Exit code 0 = all PASS, 1 = any FAIL.
5. **Read the report** — `results/<timestamp>_<name>.md`. It contains the verdict table, the
   scan/RSSI comparison against the reference, the link data and the throughput table.
6. **Report back to the user** in this shape:
   - overall PASS/FAIL,
   - the evidence lines that drove it (AP count + median RSSI delta, RSSI, TCP/UDP Mbit/s both ways),
   - for UDP, **quote the offered rate with the result** (`17.5 Mbit/s at -b 40`), never a bare number,
   - the distinction *hardware vs firmware* if something failed,
   - the report file path.

## What "correct" means

Three references, in order of authority:

1. **The reference board** (same room, same moment) — the hardware evidence.
2. **Absolute floors** from `config/thresholds.json` — sanity limits, peer-dependent.
3. **Espressif's published figures** from `config/official_throughput.json` — reference only, shown in
   the report as `official air` / `official shield`. They were measured on old IDF commits (S3:
   2021-04-21, C6: 2024-12-14), the shield-box column needs a router peer + clean RF, and they must
   **never** be used to fail a board. Use the *air* column as the yardstick for an ESP↔ESP test.

**UDP needs the offered load set high enough (`-b`).** In `iperf -c <ip> -u -b 40`, `-b` is the rate
iperf is *asked to send at*, and iperf never reports more than it was asked to send — so a low offer
**caps the measurement**: an ESP32-S3 that can do 30 Mbit/s still reports ~20 at `-b 20` and looks
broken. Run UDP at **`-b 40`** (the config default) so the link, not the offer, is the limit, and use
the `udp_bitrates` sweep (20/30/40) to see the ramp and the plateau. Judge a UDP number only together
with the offer it was measured at, and never hand-copy a `-b 20` result as if it were a ceiling.

| Observation | Conclusion |
|---|---|
| All verdicts PASS; TCP TX ≈ RX; TCP near the official air figure (20 Mbit/s for S3) | WiFi hardware correct for this chip class |
| Low absolute Mbit/s but good ratio vs reference | peer/socket-buffer limited, **not** a DUT fault |
| UDP below official 30 Mbit/s but `-b` offered was below 30 | **inconclusive** — the offer capped it; rerun at `-b 40` before judging |
| UDP plateaus well below 30 Mbit/s **even at `-b 40`** | real link ceiling — compare with the reference board and report both |
| AP count low or median RSSI delta > 8 dB vs reference | RF path fault (antenna/matching/FEM/module) |
| Association/ping fine, throughput poor and one-sided | RF retries — layout, interference, power integrity |
| Panics in wifi/supplicant (`rsn_selector_to_bitfield`, `cnx_get_authtype_strength`) | firmware/library issue, not RF — do not blame the antenna |
| No connection at all, console silent | power/reset/wiring, or DTR/RTS held asserted |

Never claim "hardware is fine" from an absolute number alone — always show the reference comparison,
and quote the IDF version the numbers came from.

## Failure playbook

1. **`Could not open COMx / PermissionError(13)`** → a serial monitor still owns the port
   (VS Code Monitor, idf.py monitor, PuTTY). Close it; verify with `mode COMx`.
2. **Association hangs, console stops responding** → stale WiFi config in NVS:
   rerun with `--erase-nvs` (erases `0x9000`/`0x6000`). This fixed a board that would not associate.
3. **No console output after flashing** → the capture asserted DTR/RTS. On boards with direct
   `RTS→EN` / `DTR→BOOT0` wiring: DTR=False + RTS=False = run, DTR=True + RTS=False = download mode,
   RTS=True = held in reset. The harness already releases both.
4. **DUT panics during scan/connect** → record it, then use `"scan_command": "scan"` (not
   `sta_scan`), keep the SoftAP at `"pmf": "off"`, and test the product firmware's own scan/connect
   path to see whether the crash is in the product stack too.
5. **`iperf: invalid argument "20M"`** → `-b` wants a bare number in Mbit/s: `-b 40`. Remember `-b` is
   the **offered** load, so a value below the expected capacity produces a capped, misleading UDP
   result — see the UDP rule above.
6. **Wrong ESP-IDF version** (build errors, or `dependencies.lock` rewritten to e.g. `5.4.1`) →
   `idf.py fullclean`, delete `sdkconfig`, `git checkout -- dependencies.lock`, rebuild with v5.5.4.
7. **Impossible throughput (e.g. 1000+ Mbit/s)** → a leftover iperf server from an earlier run; the
   parser drops summaries whose interval does not match, but don't hand-copy raw lines.

## Guardrails

- Test **one** DUT against **one** reference, same room, same run. Do not compare two runs hours apart.
- Do not modify the harness's parser to make a failing number pass; adjust `config/thresholds.json`
  only when the user explicitly agrees on new acceptance floors, and say so in the report.
- If the user's board runs product firmware and you flashed the test firmware over it, say so and
  give the restore command.
