# Manual test — no harness, no Python

Run exactly what `wifi_hw_test.py` automates, using two serial terminals at **115200**.
**DUT** = board under test · **REF** = known-good reference board.

> Prefer automation? The **[`execute-wifi-hw-test`](../.claude/skills/execute-wifi-hw-test/SKILL.md)
> AI skill** (or the harness) does all of this for you — see [`../README.md`](../README.md).

---

## 1. Firmware onto both boards

Stock ESP-IDF `wifi/iperf` example, vendored in this repo with its components so it builds offline:

```bash
cd firmware/iperf-esp32s3 && idf.py set-target esp32s3 && idf.py -p COM8  flash && cd ../..
cd firmware/iperf-esp32c6 && idf.py set-target esp32c6 && idf.py -p COM10 flash && cd ../..
```

Other chip (from the repo root):

```bash
python tools/prepare_firmware.py --target esp32c3 --dest firmware/iperf-esp32c3 \
    --idf-path <IDF_PATH> --tools-path <IDF_TOOLS_PATH> --python-env <IDF_VENV> --build
```

## 2. Type this (one terminal per board)

```
DUT: wifi_mode sta
DUT: scan                                    # note AP count + RSSI of a few known APs
REF: wifi_mode sta
REF: scan                                    # same APs? RSSI within a few dB of the DUT?

REF: wifi_mode ap
REF: ap_set WIFI_HW_TEST test12345 -a wpa2 --disable_pmf

DUT: sta_connect WIFI_HW_TEST test12345      # expect "sta ip: 192.168.4.X" plus an rssi line
DUT: ping 192.168.4.1

# TCP  DUT -> REF , then REF -> DUT   (X = the DUT IP from above)
REF: iperf --abort
REF: iperf -s -i 2
DUT: iperf --abort
DUT: iperf -c 192.168.4.1 -i 2 -t 15
DUT: iperf --abort
DUT: iperf -s -i 2
REF: iperf --abort
REF: iperf -c 192.168.4.X -i 2 -t 15

# UDP  DUT -> REF , then REF -> DUT   (same pattern, -u -b 20)
REF: iperf --abort
REF: iperf -s -u -i 2
DUT: iperf --abort
DUT: iperf -c 192.168.4.1 -u -b 20 -i 2 -t 10
DUT: iperf --abort
DUT: iperf -s -u -i 2
REF: iperf --abort
REF: iperf -c 192.168.4.X -u -b 20 -i 2 -t 10
```

## 3. Pass / fail

| Check | PASS when |
|---|---|
| scan vs reference | DUT APs ≥ 60 % of REF **and** median RSSI delta on common BSSIDs ≤ 8 dB (≤ 20 dB if REF has a better antenna) |
| association | IP obtained, link RSSI ≥ −70 dBm |
| ping | loss ≤ 5 % |
| TCP TX / RX | ≥ 15 Mbit/s (ESP32-S3 floor; see [`../config/thresholds.json`](../config/thresholds.json)) |
| UDP TX / RX | ≥ 12 Mbit/s (ESP32-S3 floor) |
| no panic | no `Guru Meditation` in either console |

Reference run, ESP32-S3 custom board ↔ ESP32-C6-DevKitC-1, ~0.5 m, RSSI −32 dBm:
scan 15 vs 14 APs (median delta −11 dB, chip antenna vs devkit antenna) · ping 5/5 · TCP
**24.2 / 20.5 Mbit/s** · UDP **17.6 / 17.4 Mbit/s**. Full report:
[`../results/20260911-142044_astra-s3-vs-c6-devkit.md`](../results/20260911-142044_astra-s3-vs-c6-devkit.md)

Reading it: low Mbit/s with TX ≈ RX means the peer or socket buffers limit the result, not your
board. RSSI clearly worse than REF means antenna/matching. Crashes in the WiFi/supplicant code are
firmware, not RF.

## 4. Traps

1. **DTR/RTS**: a terminal that asserts them holds the chip in reset (boards wired `RTS→EN`,
   `DTR→BOOT0`) — you get no output. Release both when opening the port.
2. **Busy port**: close VS Code Monitor / `idf.py monitor` before flashing.
3. **`sta_connect` hangs with no events** → stale NVS:
   `esptool.py --chip esp32s3 -p COM8 erase_region 0x9000 0x6000`, then retry.
4. **`-b` wants a number** (`-b 20`, not `20M`), and always `iperf --abort` before a new role.
5. **Same room, same time** — RSSI and throughput are only comparable within one session.
