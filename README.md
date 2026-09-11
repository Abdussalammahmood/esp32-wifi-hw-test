# esp32-wifi-hw-test

Verify the WiFi **hardware** of an ESP32 board by testing it against a known-good reference board.
Two boards, one command, and a markdown report with PASS/FAIL verdicts.

Use it for: new board bring-up, antenna changes, a batch that fails certification, or a
"WiFi doesn't work" report.

> **Helping AI skill:** [`.claude/skills/execute-wifi-hw-test/SKILL.md`](.claude/skills/execute-wifi-hw-test/SKILL.md)
> — an agent picks the ports, builds/flashes both boards, runs the tests and tells you whether the
> DUT is correct. Prefer to do it yourself? Follow
> **[`docs/MANUAL_TESTING.md`](docs/MANUAL_TESTING.md)**.

## How it works

- **Reference board** (known good): scans the room, then becomes a SoftAP + iperf server.
- **DUT**: scans the same room, associates to the reference, then ping + TCP/UDP in both directions.
- Both boards run the stock IDF `wifi/iperf` example (source included in `firmware/`), so a bad
  result is a **hardware** result, not an application bug.
- Verdicts come from (1) AP count and RSSI delta vs the reference, measured in the same room at the
  same moment, and (2) per-chip throughput floors in `config/thresholds.json`.

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

## Pass / fail rules (what the report applies)

| Test | PASS when |
|---|---|
| scan vs reference | DUT APs ≥ 60 % of the reference **and** median RSSI delta ≤ 8 dB (relax to 20 dB if the reference has a better antenna) |
| association | IP obtained and link RSSI ≥ −70 dBm |
| ping | loss ≤ 5 % |
| TCP TX / RX | ≥ chip floor (ESP32-S3: 15 Mbit/s) |
| UDP TX / RX | ≥ chip floor (ESP32-S3: 12 Mbit/s) |
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
  "reference_baseline": false,          // true = also measure the reference in the client role
  "thresholds_file": "config/thresholds.json"
}
```

The reference board creates the SoftAP, so **no real network credentials are needed**.

## Gotchas

1. Open serial ports with **DTR/RTS released** — on boards wired `RTS→EN` / `DTR→BOOT0` they reset
   the chip and the console stays silent. The harness does this for you.
2. Close VS Code Monitor / `idf.py monitor` before flashing, or the port is busy.
3. `sta_connect` hanging means stale NVS → `--erase-nvs`.
4. Use `scan`, not `sta_scan` (the latter can panic the supplicant on some builds).
5. `iperf -b 20` (not `20M`), and `iperf --abort` before switching roles.
6. Compare only within one run — the harness scans both boards back to back for this reason.

## Layout

```
wifi_hw_test.py                  harness -> results/<run>.md
config/                          run config + per-chip acceptance floors
firmware/iperf-esp32s3|esp32c6/  test firmware source (builds offline)
docs/MANUAL_TESTING.md           do the whole test by hand, no Python
.claude/skills/execute-wifi-hw-test/SKILL.md   AI skill: run it and judge the result
tools/prepare_firmware.py        generate firmware for any other target
results/                         a real reference run (PASS report)
```
