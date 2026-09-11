# esp32-wifi-hw-test

Verify the WiFi **hardware** of an ESP32 board by testing it against a known-good reference board.
Two boards, one command, and a markdown report with PASS/FAIL verdicts.

Use it for: new board bring-up, antenna changes, a batch that fails certification, or a
"WiFi doesn't work" report.

> **Helping AI skill:** [`.claude/skills/execute-wifi-hw-test/SKILL.md`](.claude/skills/execute-wifi-hw-test/SKILL.md)
> — an agent picks the ports, builds/flashes both boards, runs the tests and tells you whether the
> DUT is correct. Prefer to do it yourself? Follow
> **[`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md)** (includes VS Code + ESP-IDF v5.5.4 steps).

## How it works

- **Reference board** (known good): scans the room, then becomes a SoftAP + iperf server.
- **DUT**: scans the same room, associates to the reference, then ping + TCP/UDP in both directions.
- Both boards run the stock IDF `wifi/iperf` example (source included in `firmware/`), so a bad
  result is a **hardware** result, not an application bug.
- Verdicts come from (1) AP count and RSSI delta vs the reference, measured in the same room at the
  same moment, and (2) per-chip throughput floors in `config/thresholds.json`.

The report also lists, **for reference only**, Espressif's official figures for the chip and the exact
firmware provenance (ESP-IDF / WiFi firmware / PHY version). Official values never affect PASS/FAIL.

## Requirements

| Item | Notes |
|---|---|
| 2× ESP32 boards | reference = any known-good board. Different chips are fine. |
| **ESP-IDF v5.5.4** | the version the vendored firmware and reference results were built with. Another version = build errors or non-comparable numbers (see [manual §1](docs/MANUAL_TESTING.md)). |
| Python 3.9+ with `pyserial` | `pip install -r requirements.txt` |
| Two free COM ports | and no monitor holding them |

## Quick start

```bash
pip install -r requirements.txt
python wifi_hw_test.py --list-ports                   # find your two COM ports

# build + flash the included firmware (details in firmware/README.md)
cd firmware/iperf-esp32s3 && idf.py set-target esp32s3 && idf.py -p COM8  flash && cd ../..
cd firmware/iperf-esp32c6 && idf.py set-target esp32c6 && idf.py -p COM10 flash && cd ../..

cp config/astra_s3_vs_c6.json config/my_board.json    # set ports, names, chips
python wifi_hw_test.py --config config/my_board.json --out results/
# exit 0 = every verdict PASS; report written to results/<timestamp>_<name>.md
```

- Different chips? Build a firmware copy with `tools/prepare_firmware.py`.
- Association hangs? Add `--erase-nvs` (stale WiFi config).
- Want the UDP ceiling? `"udp_bitrates": [20, 30, 40]` in the config runs an offered-load sweep.

## Official Espressif figures (reference only)

From the ESP-IDF docs *Wi-Fi Performance and Power Save* (per-target page), iperf example, one
stream — **air / shield-box**, Mbit/s:

| Chip | UDP RX | UDP TX | TCP RX | TCP TX |
|---|---|---|---|---|
| **ESP32-S3** | 30 / 88 | 30 / 98 | 20 / 73 | 20 / 83 |
| **ESP32-C6** | 30 / 63 | 30 / 51 | 20 / 46 | 20 / 43 |
| ESP32-C3 | 30 / 50 | 30 / 40 | 20 / 35 | 20 / 37 |
| ESP32-S2 | 30 / 70 | 30 / 50 | 20 / 32 | 20 / 37 |
| ESP32 | 30 / 85 | 30 / 75 | 20 / 65 | 20 / 75 |
| ESP32-C2 | not published | | | |

`air` = best over-the-air result in Espressif's lab; `shield` = shielded box with an ASUS RT-N66U
router. Our ESP↔ESP SoftAP test at ~0.5 m should be judged against the **air** column — the shield-box
numbers need a router peer and clean RF. Data + IDF commit stamps:
[`config/official_throughput.json`](config/official_throughput.json) (the tables were measured on IDF
commit `15575346`, 2021-04-21 for S3, and `7ff0a07d`, 2024-12-14 for C6 — **not** on v5.5.4).

## Pass / fail rules (what the report applies)

| Test | PASS when |
|---|---|
| scan vs reference | DUT APs ≥ 60 % of the reference **and** median RSSI delta ≤ 8 dB (relax to 20 dB if the reference has a better antenna) |
| association | IP obtained and link RSSI ≥ −70 dBm |
| ping | loss ≤ 5 % |
| TCP TX / RX | ≥ chip floor (ESP32-S3: 15 Mbit/s; docs' air figure 20) |
| UDP TX / RX | ≥ chip floor (ESP32-S3: 12 Mbit/s; docs' air figure 30 — raise the offered rate to compare) |
| no panic | no `Guru Meditation` on either board |

Reading the result: low Mbit/s but TX ≈ RX means the peer or socket buffers are the limit, not your
board. RSSI clearly worse than the reference means antenna/matching. Crashes in the WiFi/supplicant
code are a firmware issue, not RF.

## Config

```jsonc
{
  "name": "my-run",
  "dut":       { "name": "...", "port": "COM8",  "chip": "esp32s3" },
  "reference": { "name": "...", "port": "COM10", "chip": "esp32c6" },
  "ap": { "ssid": "WIFI_HW_TEST", "password": "test12345", "ip": "192.168.4.1", "pmf": "off" },
  "scan_command": "scan",
  "durations": { "scan": 15, "ping": 12, "interval": 2, "tcp": 15, "udp": 10, "udp_bitrate": 20 },
  "udp_bitrates": [20, 30, 40],         // optional offered-load sweep (reference only)
  "reference_baseline": false,          // true = also measure the reference in the client role
  "thresholds_file": "config/thresholds.json",
  "official_file":   "config/official_throughput.json"
}
```

The reference board creates the SoftAP, so **no real network credentials are needed**.

## Gotchas

1. **Wrong ESP-IDF version** — use v5.5.4; another version rewrites `dependencies.lock` and changes
   the numbers. Clean with `idf.py fullclean` + delete `sdkconfig` before rebuilding.
2. Open serial ports with **DTR/RTS released** — on boards wired `RTS→EN` / `DTR→BOOT0` they reset
   the chip and the console stays silent. The harness does this for you.
3. Close VS Code Monitor / `idf.py monitor` before flashing, or the port is busy.
4. `sta_connect` hanging means stale NVS → `--erase-nvs`.
5. Use `scan`, not `sta_scan` (the latter can panic the supplicant on some builds).
6. `iperf -b 20` (not `20M`), and `iperf --abort` before switching roles.
7. Compare only within one run — the harness scans both boards back to back for this reason.

## Layout

```
wifi_hw_test.py                  harness -> results/<run>.md
config/                          run config, acceptance floors, official chip figures
firmware/iperf-esp32s3|esp32c6/  test firmware source (builds offline, IDF v5.5.4)
docs/MANUAL_TESTING.md           do the whole test by hand, no Python (+ VS Code steps)
.claude/skills/execute-wifi-hw-test/SKILL.md   AI skill: run it and judge the result
tools/prepare_firmware.py        generate firmware for any other target
results/                         a real reference run (PASS report)
```
