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
| IDF location | e.g. `D:\IDF_...\esp-idf`, tools root, venv python |

Do **not** guess ports. Two boards on the wrong ports produce a confident but meaningless report.

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
   chips, durations, `reference_baseline`.
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
   - the distinction *hardware vs firmware* if something failed,
   - the report file path.

## What "correct" means

| Observation | Conclusion |
|---|---|
| All verdicts PASS; TCP TX ≈ RX | WiFi hardware correct for this chip class |
| Low absolute Mbit/s but good ratio vs reference | peer/socket-buffer limited, **not** a DUT fault |
| AP count low or median RSSI delta > 8 dB vs reference | RF path fault (antenna/matching/FEM/module) |
| Association/ping fine, throughput poor and one-sided | RF retries — layout, interference, power integrity |
| Panics in wifi/supplicant (`rsn_selector_to_bitfield`, `cnx_get_authtype_strength`) | firmware/library issue, not RF — do not blame the antenna |
| No connection at all, console silent | power/reset/wiring, or DTR/RTS held asserted |

Never claim "hardware is fine" from an absolute number alone — always show the reference comparison.

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
5. **`iperf: invalid argument "20M"`** → `-b` wants a number: `-b 20`.
6. **Impossible throughput (e.g. 1000+ Mbit/s)** → a leftover iperf server from an earlier run; the
   parser drops summaries whose interval does not match, but don't hand-copy raw lines.

## Guardrails

- Test **one** DUT against **one** reference, same room, same run. Do not compare two runs hours apart.
- Do not modify the harness's parser to make a failing number pass; adjust `config/thresholds.json`
  only when the user explicitly agrees on new acceptance floors, and say so in the report.
- If the user's board runs product firmware and you flashed the test firmware over it, say so and
  give the restore command.
