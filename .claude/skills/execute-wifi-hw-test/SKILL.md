---
name: execute-wifi-hw-test
description: |
  Run the ESP32 WiFi hardware verification harness (esp32-wifi-hw-test) against a device under
  test and a known-good reference board, then judge whether the DUT's WiFi hardware is correct.
  Covers: establishing the ESP-IDF v5.5.4 toolchain the USER has installed (ask for the paths and
  validate them — the build depends on it and the repo cannot supply it), finding COM ports,
  building/flashing the vendored wifi/iperf firmware on both boards, writing the run config,
  executing wifi_hw_test.py, reading the generated markdown report, and the failure playbook
  (stale NVS, busy port, supplicant panics, DTR/RTS traps). Falls back to docs/MANUAL_TESTING.md
  when the user wants to run the test by hand.
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
| **ESP-IDF v5.5.4 install — where is it?** | **ask the user** (checkout, `IDF_TOOLS_PATH`, venv python) and validate all three — see **Step 0**. The build depends on an install that lives *outside* this repo; nothing here can substitute for it |
| Which IDF version is it? | `idf.py --version` must print **ESP-IDF v5.5.4** — the version the vendored firmware and the reference results were built with |

Do **not** guess ports. Two boards on the wrong ports produce a confident but meaningless report.
Do **not** guess the IDF paths either — ask, then prove they work (Step 0).

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

## Step 0 — the ESP-IDF toolchain (ask the user, then prove it)

Building and flashing depend on an **ESP-IDF v5.5.4 install that lives outside this repo**. The repo
cannot supply it, so this is the one input you must get from the user before promising a run.

**Ask for these three paths** (if `IDF_PATH`, `IDF_TOOLS_PATH`, `IDF_PYTHON_ENV_PATH` are already set
in a shell, show the user those values and ask them to confirm instead of asking blind):

| Ask for | Typical value on this machine | Prove it with |
|---|---|---|
| ESP-IDF checkout | `D:\IDF_5_5_AI\.espressif\v5.5.4\esp-idf` | `export.ps1` exists **and** `git -C <idf> describe --tags` → `v5.5.4` |
| `IDF_TOOLS_PATH` (tools root) | `C:\Espressif` | directory exists, holds `tools\` — **no spaces anywhere in the path** |
| `IDF_PYTHON_ENV_PATH` (venv) | `C:\Espressif\tools\python\v5.5.4\venv` | `Scripts\python.exe` exists |

Then activate it in the shell you build from and check the version:

```powershell
$env:IDF_TOOLS_PATH='C:\Espressif'
$env:IDF_PYTHON_ENV_PATH='C:\Espressif\tools\python\v5.5.4\venv'
$env:IDF_PYTHON_CHECK_CONSTRAINTS='no'
. "D:\IDF_5_5_AI\.espressif\v5.5.4\esp-idf\export.ps1"
idf.py --version          # must print: ESP-IDF v5.5.4
```

- **Version must be v5.5.4.** Another version rewrites `firmware/*/dependencies.lock`
  (`version: 5.5.4` → `5.4.1`) and changes Wi-Fi behaviour, so the numbers stop being comparable to
  the committed reference run.
- **If the user has no ESP-IDF installed:** the build/flash path is impossible until they install
  v5.5.4 (Espressif installer, or `git clone -b v5.5.4 --recursive` + `install.ps1`). Say so plainly
  and offer the alternative: they flash the vendored firmware themselves and you run the test on the
  already-flashed boards, or they follow `docs/MANUAL_TESTING.md`.
- **Check the ports too, not just the build:** flashing needs the COM port free (§ failure playbook 1).
- **Record what you used** — checkout path and `idf.py --version` — in the report you hand back, next
  to the harness's own provenance lines (it prints the IDF/wifi-firmware/PHY from each board).

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
   A full run takes ~4 min (the UDP offered-load sweep alone is ~1 min and is reference-only
   information). `--fast` (tcp 8 s, udp 6 s, ping 6 s, no sweep) gives a PASS/FAIL in ~2.5 min — use it
   to smoke-test a board or a fix, **not** for the number you report: the shorter windows are noisier
   and can graze a floor (seen: TCP RX 14.98 vs the 15 Mbit/s floor on a healthy board).
5. **Read the report** — `results/<timestamp>_<name>.md`. It contains the verdict table, the
   scan/RSSI comparison against the reference, the link data and the throughput table.
6. **Report back to the user** in this shape:
   - overall PASS/FAIL — and note that it is the AND of **every** verdict row,
   - the evidence lines that drove it (AP count + median RSSI delta, RSSI, TCP/UDP Mbit/s both ways),
   - any `no panic on <board>` failure: which board, which phase, and how many runs it happened in,
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
| **0 APs and no `SCAN_DONE` line**, or console output stops mid-line | the chip **reset during the scan** — read the reset reason, look for `E BOD: Brownout detector was triggered`. That is a **power** fault, not an antenna one |
| **0 APs but association/iperf work fine** | not a radio fault: the driver refused to scan (`STA is connecting, scan are not allowed!` / `ESP_ERR_WIFI_STATE`, `DONE.STA_SCAN_START,FAIL.12294`). The board was left associated by the previous run. Fixed in the harness (`sta_disconnect` before every scan) — if you see it, you are running an old copy |
| AP-count ratio just under the floor while the median RSSI delta passes (e.g. 13 vs 22 APs = 59% vs 60%) | the reference's antenna out-hears the DUT, so the *count* ratio is not a valid discriminator for a mixed-antenna pair. Judge on the shared-BSSID RSSI delta, or run the same board as the DUT to get a conclusive number |
| Throughput collapses in one direction only (e.g. RX ~1.5 Mbit/s, TX fine) | check for resets/brownout first; a supply that sags under load looks like an RF fault |
| Association/ping fine, throughput poor and one-sided | RF retries — layout, interference, power integrity |
| Panics in wifi/supplicant (`rsn_selector_to_bitfield`, `cnx_get_authtype_strength`) | firmware/library issue, not RF — do not blame the antenna |
| `no panic on <board>` FAIL while **every** throughput number passed | the board crashed during the run (association / reconnect path). The run is a **FAIL overall** even though the numbers look healthy — report the crash, and re-run to see how often it happens |
| No connection at all, console silent | power/reset/wiring, or DTR/RTS held asserted |

Never claim "hardware is fine" from an absolute number alone — always show the reference comparison,
and quote the IDF version the numbers came from. **Read the whole verdict table**: the overall result
is the AND of every row, so a `no panic` failure fails the run even when scan, ping and all four
throughput numbers passed.

## Failure playbook

1. **`Could not open COMx / PermissionError(13)`** → a serial monitor still owns the port
   (VS Code Monitor, idf.py monitor, PuTTY). Close it; verify with `mode COMx`.
2. **Association hangs, console stops responding** → stale WiFi config in NVS:
   rerun with `--erase-nvs` (erases `0x9000`/`0x6000`). This fixed a board that would not associate.
   **Reruns should start from a clean state** — a finished run leaves the DUT associated, and the next
   run's scan is refused until it is disconnected or the state is erased. The harness now sends
   `sta_disconnect` before each scan, and `--erase-nvs` is the more thorough option.
2b. **`esptool` cannot connect at all** (`Failed to connect … Serial data stream stopped: Possible
   serial noise or corruption`) → the debug adapter's **DTR/RTS are not reaching BOOT0/EN**. Seen on
   the Astra board after the power wiring was changed. Consequences: `--erase-nvs` fails, `idf.py flash`
   fails (fix the wiring, or enter download mode by holding BOOT0/GPIO0 low through a reset), and the
   harness's chip/MAC probe silently falls back to reading the console banner. The test itself still
   works — it only needs the console UART.
3. **No console output after flashing** → the capture asserted DTR/RTS. On boards with direct
   `RTS→EN` / `DTR→BOOT0` wiring: DTR=False + RTS=False = run, DTR=True + RTS=False = download mode,
   RTS=True = held in reset. The harness already releases both.
4. **DUT panics during scan/connect** → record it, then use `"scan_command": "scan"` (not
   `sta_scan`), keep the SoftAP at `"pmf": "off"`, and test the product firmware's own scan/connect
   path to see whether the crash is in the product stack too. It is **intermittent**: the same board
   was seen to panic once at association and pass cleanly on the next run, so report the frequency
   (`failed 1 of 3 runs`) rather than calling the board broken. The harness records it as a
   `no panic on <board>` verdict and fails that run.
5. **`iperf: invalid argument "20M"`** → `-b` wants a bare number in Mbit/s: `-b 40`. Remember `-b` is
   the **offered** load, so a value below the expected capacity produces a capped, misleading UDP
   result — see the UDP rule above.
6. **Wrong ESP-IDF version, or no working IDF at all** →
   - `idf.py: command not found` / `idf.py` errors about a missing toolchain → the shell was not
     activated: run the Step 0 block first (`. <idf>\export.ps1`).
   - `cc1.exe: fatal error: Both 'XTENSA_GNU_CONFIG' and "-dynconfig=" specified but pointed
     different files` → `IDF_TOOLS_PATH` (or the IDF checkout) sits under a path **with a space**, e.g.
     `C:\Users\Abdus Salam\.espressif`: ninja passes the compiler in 8.3 short form and the xtensa
     driver gives up. Move the tools root somewhere space-free (e.g. `C:\Espressif`) and rebuild.
   - `dependencies.lock` rewritten to e.g. `5.4.1` → `idf.py fullclean`, delete `sdkconfig`,
     `git checkout -- dependencies.lock`, rebuild with v5.5.4.
7. **Impossible throughput (e.g. 1000+ Mbit/s)** → a leftover iperf server from an earlier run; the
   parser drops summaries whose interval does not match, but don't hand-copy raw lines.
8. **`E BOD: Brownout detector was triggered`, `rst:0x3 (RTC_SW_SYS_RESET)`, a scan that returns
   0 APs / never prints `SCAN_DONE`, or console output that stops mid-line** → the supply sagged
   under radio load and the chip reset. Real case on the Astra board: run 1 PASS (13 APs), run 2
   degraded (9 APs, no association), runs 3-5 the DUT died during the scan while the reference kept
   hearing 11-18 APs. Treat it as a **power-integrity fault**, not antenna/firmware:
   - check how the board is powered (proper supply vs back-feeding through the UART/USB header),
   - check the 3V3 rail's bulk capacitance and the regulator's transient response,
   - retest on a stiff bench supply / short cable,
   - **never "fix" it by disabling the brownout detector** — that removes the protection that found
     the fault. Passing only with BOD off *is* the diagnosis.

## Guardrails

- Test **one** DUT against **one** reference, same room, same run. Do not compare two runs hours apart.
- Do not modify the harness's parser to make a failing number pass; adjust `config/thresholds.json`
  only when the user explicitly agrees on new acceptance floors, and say so in the report.
- If the user's board runs product firmware and you flashed the test firmware over it, say so and
  give the restore command.
