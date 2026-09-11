# esp32-wifi-hw-test

Hardware-verification harness for **ESP32 WiFi radios**. You give it two boards — one to test
(*DUT*) and one known-good board (*reference*) — and it produces a markdown report that says, per
test, whether the DUT is **correct or not**, with the evidence.

It answers questions like:

- Is the radio/antenna on this new board actually working, or is it firmware?
- Is this board's sensitivity comparable to a known-good board in the same room, at the same moment?
- Does it sustain real TCP/UDP throughput in **both** directions?

Typical use: first articles of a custom board, antenna changes, a batch that fails certification,
or validating a "WiFi doesn't work" bug report.

---

## 1. How it works

```
        reference board (known good)                     DUT (board under test)
        ┌──────────────────────────┐                     ┌──────────────────────────┐
        │ wifi/iperf example       │   SoftAP + iperf    │ wifi/iperf example       │
        │ - scans first            │◄──── server ────────│ - scans first            │
        │ - then becomes SoftAP    │                     │ - associates as STA      │
        └──────────────────────────┘                     └──────────────────────────┘
                     ▲                                             ▲
                     └────────── USB serial consoles ─────────────┘
                                        │
                              wifi_hw_test.py (this repo)
                                        │
                                        ▼
                       results/<timestamp>_<name>.md  (verdicts + raw evidence)
```

Both boards run the unmodified ESP-IDF `wifi/iperf` example (its console provides `scan`,
`sta_connect`, `wifi_mode`, `ap_set`, `ping`, `iperf`). The harness drives both consoles over UART,
so it never touches your product firmware.

Two independent verdict sources:

1. **Reference comparison (hardware truth).** Both boards scan the *same* APs at the *same* moment.
   The report computes the DUT/reference AP-count ratio and the median RSSI delta on common BSSIDs.
   A board with a bad antenna/matching shows up here immediately, regardless of chip type.
2. **Absolute throughput floors + optional same-role baseline.** Per-chip floors live in
   `config/thresholds.json`. If you have a reference of the **same chip**, set
   `"reference_baseline": true` and the harness also measures the reference board *in the client
   role* under identical conditions, then requires `DUT >= ref_ratio_min × reference`.

## 2. Requirements

| Item | Notes |
|---|---|
| 2× ESP32 boards | reference = any known-good board (DevKit). DUT = the board under test. Different chips are fine. |
| USB-UART access to both | two COM ports, 115200 baud |
| Python 3.9+ with `pyserial` | `pip install pyserial` (the ESP-IDF venv already has it) |
| ESP-IDF (to build/flash the test firmware) | v5.x; `esptool` optional but recommended for board identity |
| Same 2.4 GHz environment | run DUT and reference simultaneously; never compare across rooms/times |

**Wired DTR/RTS caution.** Some custom boards wire the debug header straight to the chip
(`RTS→EN`, `DTR→BOOT0`). Asserting those lines holds the chip in reset and the console stays silent.
This harness always opens ports with **DTR=False, RTS=False**; if you write your own capture code,
do the same.

## 3. Quick start

```bash
# 1. find your ports
python wifi_hw_test.py --list-ports

# 2. put the test firmware on both boards (see firmware/README.md)
python tools/prepare_firmware.py --target esp32s3 --dest firmware/iperf-esp32s3
python tools/prepare_firmware.py --target esp32c6 --dest firmware/iperf-esp32c6
#    then, from each dest: idf.py -p <PORT> flash

# 3. copy a config and set your ports / board names
cp config/astra_s3_vs_c6.json config/my_board.json

# 4. run it
python wifi_hw_test.py --config config/my_board.json --out results/
#    exit code 0 = all verdicts PASS, 1 = something FAILED (CI friendly)
```

If association hangs (console stops answering, no connect events) the board has a stale WiFi
config in NVS — rerun with `--erase-nvs`.

Output: `results/<timestamp>_<name>.md` — see `results/` for a real example produced by this repo.

## 4. Config reference

```jsonc
{
  "name": "my-run-name",                       // used in the report filename/title
  "dut":       { "name": "...", "port": "COM8",  "chip": "esp32s3" },
  "reference": { "name": "...", "port": "COM10", "chip": "esp32c6" },

  "ap": { "ssid": "WIFI_HW_TEST", "password": "test12345",
          "ip": "192.168.4.1", "pmf": "off" },  // SoftAP created BY the reference board
                                                  // -> no real network credentials needed

  "scan_command": "scan",                       // "scan" (safe) or "sta_scan" (crashes on some builds)
  "tests":  { "scan": true, "assoc": true, "ping": true,
              "tcp_tx": true, "tcp_rx": true, "udp_tx": true, "udp_rx": true },

  "durations": { "scan": 15, "ping": 12, "interval": 2,
                 "tcp": 15, "udp": 10, "udp_bitrate": 20 },

  "reference_baseline": false,                  // true = also measure reference in the client role
  "thresholds_file": "config/thresholds.json",

  "esptool": { "python": "<venv python>", "path": "<idf>/components/esptool_py/esptool/esptool.py" },
  "nvs_offset": "0x9000", "nvs_size": "0x6000"
}
```

`thresholds.json` (per chip class):

```jsonc
"esp32s3": { "tcp_min_mbit": 15, "udp_min_mbit": 12 },
"default": { "scan_ap_ratio_min": 0.6, "rssi_delta_db_max": 8, "min_rssi_dbm": -70,
             "ping_loss_pct_max": 5, "ref_ratio_min": 0.7 }
```

## 5. How results are judged

| Test | PASS when |
|---|---|
| scan (RX sensitivity vs reference) | DUT AP count ≥ `scan_ap_ratio_min` × reference **and** median RSSI delta on common BSSIDs ≤ `rssi_delta_db_max` dB |
| association | an IP is obtained and link RSSI ≥ `min_rssi_dbm` |
| ping | loss ≤ `ping_loss_pct_max` |
| TCP TX / RX | avg ≥ chip `tcp_min_mbit`; with baseline: also ≥ `ref_ratio_min` × reference |
| UDP TX / RX | avg ≥ chip `udp_min_mbit`; with baseline: also ≥ `ref_ratio_min` × reference |
| no panic | zero `Guru Meditation` events on either board during the run |

**Interpreting it**

- **Everything PASS, TX≈RX:** radio, matching and antenna are good. A low *absolute* number with a
  good *ratio* means the peer or the socket buffers are the limit, not your board.
- **RSSI delta vs reference is large (> 8 dB) or AP count is low:** RF path problem — antenna,
  matching, RF switch/FEM, or a damaged module. Hardware issue.
- **Scan/association fine, throughput poor and asymmetric:** retries at RF level (interference,
  layout, power integrity) rather than a dead radio.
- **Panics in the WiFi/supplicant code:** that is a firmware/library issue, not RF. Report upstream;
  do not chase the antenna.

## 6. AI skill

`.claude/skills/execute-wifi-hw-test/SKILL.md` teaches an agent to run this harness end-to-end:
check ports, prepare/flash firmware, run the tests, read the generated report, and state whether the
DUT is correct — including the failure playbook (stale NVS, busy port, panics).

## 7. Gotchas learned the hard way

1. **DTR/RTS reset trap** — a default `pyserial` open asserts both lines; on boards with direct
   `RTS→EN` / `DTR→BOOT0` wiring that resets the chip or parks it in download mode → zero output.
2. **A serial monitor holding the port** blocks flashing (`PermissionError(13)`). Check with
   `mode COMx` / `--list-ports` and close it.
3. **Stale WiFi config in NVS** makes `sta_connect` hang silently. `--erase-nvs` fixes it.
4. **`sta_scan` can panic the supplicant** (`rsn_selector_to_bitfield`, `wpa_common.c`). Use `scan`.
5. **`iperf -b` takes a number** (`-b 20`), not `20M`.
6. **A leftover iperf server** keeps printing and can pollute the next measurement; the parser
   discards interval summaries whose span does not match the configured report interval.
7. **PMF/PMF-transition APs** have triggered crashes in the STA path on some IDF builds; the SoftAP
   is created with PMF disabled on purpose.

## 8. Limitations

- Measures 2.4 GHz only (what these chips have).
- The reference comparison is strongest when both boards are the **same chip**; with mixed chips the
  scan/RSSI comparison stays valid, throughput ratios are indicative (the report says so).
- RF numbers are only comparable within a single run — the harness scans both boards back-to-back for
  exactly this reason.
- Not a substitute for conducted RF testing (spectrum analyzer / RF test tool) for certification.

## 9. Repository layout

```
wifi_hw_test.py                     harness (drives both consoles, writes the report)
config/astra_s3_vs_c6.json          real-world example config
config/thresholds.json              acceptance floors per chip class
tools/prepare_firmware.py           copy + build the IDF wifi/iperf example for any target
firmware/README.md                  firmware preparation details
.claude/skills/execute-wifi-hw-test/SKILL.md   AI skill: run it and judge the result
results/                            generated markdown reports (one committed as a reference)
```
