# Manual test — no harness, no Python

Run exactly what `wifi_hw_test.py` automates, using two serial terminals at **115200**.
**DUT** = board under test · **REF** = known-good reference board.

> Prefer automation? The **[`execute-wifi-hw-test`](../.claude/skills/execute-wifi-hw-test/SKILL.md)
> AI skill** (or the harness) does all of this for you — see [`../README.md`](../README.md).

---

## 1. Use ESP-IDF v5.5.4 (read this first)

The vendored firmware and the results in `results/` were produced with **ESP-IDF v5.5.4**.
Check yours:

```bash
idf.py --version          # expect: ESP-IDF v5.5.4
```

**Building with a different IDF version is the most common way this test fails before it starts.**
Example: building with v5.4.1 rewrites `dependencies.lock` (`version: 5.5.4` → `5.4.1`) and can hit
Kconfig/API mismatches — and even when it compiles, the Wi-Fi numbers are then from a different
driver/PHY, so they are not comparable to the reference run.

If you did build with another version, clean up before rebuilding with v5.5.4:

```bash
cd firmware/iperf-esp32s3
idf.py fullclean
del sdkconfig            # PowerShell;  rm sdkconfig on bash
git checkout -- dependencies.lock
```

(Espressif's *official* throughput tables are stamped with even older commits — ESP32/S2/S3/C3:
`15575346` from 2021-04-21, ESP32-C6: `7ff0a07d` from 2024-12-14. They are reference figures, not a
spec for your build — see [`../config/official_throughput.json`](../config/official_throughput.json).)

### Build & flash with VS Code (ESP-IDF extension)

1. Install the **ESP-IDF** extension, then `ESP-IDF: Configure ESP-IDF Extension` →
   *Use existing setup* → select the **v5.5.4** installation.
2. `File → Open Folder…` → `firmware/iperf-esp32s3` (repeat in a second window for the other board).
3. `ESP-IDF: Set Espressif Device Target` → pick the chip (`esp32s3`, `esp32c6`, …).
4. In the bottom bar pick the COM port, then **Build** → **Flash** (or
   `ESP-IDF: Build, Flash and Monitor`).
5. **Close the VS Code Monitor before flashing the next board** — a monitor holds the port and
   flashing fails with `PermissionError(13)`.

Command-line equivalent (from the repo root):

```bash
cd firmware/iperf-esp32s3 && idf.py set-target esp32s3 && idf.py -p COM8  flash && cd ../..
cd firmware/iperf-esp32c6 && idf.py set-target esp32c6 && idf.py -p COM10 flash && cd ../..
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

# UDP  DUT -> REF , then REF -> DUT
REF: iperf --abort
REF: iperf -s -u -i 2
DUT: iperf --abort
DUT: iperf -c 192.168.4.1 -u -b 20 -i 2 -t 10
DUT: iperf --abort
DUT: iperf -s -u -i 2
REF: iperf --abort
REF: iperf -c 192.168.4.X -u -b 20 -i 2 -t 10

# UDP load sweep (find the ceiling): repeat the DUT->REF run with -b 30 and -b 40
```

## 3. Pass / fail — judged against the MCU documentation

Compare in this order:

**(a) Your reference board** — the primary evidence. Both boards scanned the same APs at the same
moment, so the DUT should match the reference on AP count and RSSI, and be in the same throughput
class. A large gap here is the hardware signal.

**(b) Espressif's published figures for the chip** — "according to the MCU documentation", reference only:

| Chip | UDP RX | UDP TX | TCP RX | TCP TX |
|---|---|---|---|---|
| **ESP32-S3** | 30 / 88 | 30 / 98 | 20 / 73 | 20 / 83 |
| **ESP32-C6** | 30 / 63 | 30 / 51 | 20 / 46 | 20 / 43 |
| ESP32-C3 | 30 / 50 | 30 / 40 | 20 / 35 | 20 / 37 |
| ESP32-S2 | 30 / 70 | 30 / 50 | 20 / 32 | 20 / 37 |
| ESP32 | 30 / 85 | 30 / 75 | 20 / 65 | 20 / 75 |
| ESP32-C2 | not published | | | |

*air / shield-box, Mbit/s.* **air** = best over-the-air result in Espressif's lab (ESP ↔ router, one
stream); **shield-box** = shielded box with an ASUS RT-N66U router. For an ESP ↔ ESP SoftAP test at
~0.5 m, the **air** column is the right yardstick; the shield-box column needs a router peer and clean
RF and is not achievable in an office. Full data + IDF commit stamps:
[`../config/official_throughput.json`](../config/official_throughput.json).

**Acceptance table:**

| Check | PASS when |
|---|---|
| scan vs REF | DUT APs ≥ 60 % of REF **and** median RSSI delta ≤ 8 dB (≤ 20 dB if REF has a better antenna) |
| association | IP obtained, link RSSI ≥ −70 dBm |
| ping | loss ≤ 5 % |
| TCP TX / RX | ≥ 15 Mbit/s (ESP32-S3 floor — the docs' ESP32-S3 air figure is 20 Mbit/s, so ~20 is expected with a good peer) |
| UDP TX / RX | ≥ 12 Mbit/s **at the offered rate** — the docs' air figure is 30 Mbit/s, so repeat with `-b 30` to check it properly |
| no panic | no `Guru Meditation` in either console |

**Reference run** (ESP32-S3 custom board ↔ ESP32-C6-DevKitC-1, IDF v5.5.4, ~0.5 m, RSSI −32 dBm):

| Test | Measured | Official air (S3) | Verdict |
|---|---|---|---|
| TCP TX | 24.2 Mbit/s | 20 | ✅ consistent / above |
| TCP RX | 20.5 Mbit/s | 20 | ✅ consistent |
| UDP TX / RX | 17.6 / 17.4 Mbit/s **offered 20** | 30 | ⚠️ inconclusive — re-run with `-b 30` |

Full report: [`../results/20260911-142044_astra-s3-vs-c6-devkit.md`](../results/20260911-142044_astra-s3-vs-c6-devkit.md).

Reading it: low Mbit/s with TX ≈ RX means the peer or socket buffers limit the result, not your
board. RSSI clearly worse than REF means antenna/matching. Crashes in the WiFi/supplicant code are
firmware, not RF. Shield-box figures are not achievable with an ESP SoftAP peer.

## 4. Traps

1. **Wrong ESP-IDF version** → see §1 (v5.5.4). Symptom: build/Kconfig errors, or `dependencies.lock`
   rewritten to another version.
2. **DTR/RTS**: a terminal that asserts them holds the chip in reset (boards wired `RTS→EN`,
   `DTR→BOOT0`) — you get no output. Release both when opening the port.
3. **Busy port**: close VS Code Monitor / `idf.py monitor` before flashing.
4. **`sta_connect` hangs with no events** → stale NVS:
   `esptool.py --chip esp32s3 -p COM8 erase_region 0x9000 0x6000`, then retry.
5. **`-b` wants a number** (`-b 20`, not `20M`), and always `iperf --abort` before a new role.
6. **Same room, same time** — RSSI and throughput are only comparable within one session.
