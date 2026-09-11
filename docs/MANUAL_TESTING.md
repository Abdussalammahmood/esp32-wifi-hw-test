# Manual test — no harness, no Python

Run exactly what `wifi_hw_test.py` automates, using two serial terminals at **115200**.

| | Board | Role in this test | Firmware to flash | Port (example) |
|---|---|---|---|---|
| **DUT** | Astra ESP32-S3 custom board | **station + traffic endpoint** — scans, connects to REF, sends/receives | [`firmware/iperf-esp32s3`](../firmware/iperf-esp32s3) | `COM8` |
| **REF** | ESP32-C6-DevKitC-1 (known good) | **access point + peer** — beacons, DHCP at `192.168.4.1`, far end of every throughput run | [`firmware/iperf-esp32c6`](../firmware/iperf-esp32c6) | `COM10` |

**Every command is typed into the terminal of the board named in the `Board` column.** There is no
remote console: `ap_set` only makes sense on REF, `sta_connect` only on the DUT. Sending a command to
the wrong board is the most common mistake in this procedure.
The console prompt is `iperf>`. Log lines carry level + timestamp (`I (1234) WIFI: …`); timestamps are
omitted below.

> Prefer automation? The **[`execute-wifi-hw-test`](../.claude/skills/execute-wifi-hw-test/SKILL.md)
> AI skill** (or the harness) does all of this for you — see [`../README.md`](../README.md).

---

## 1. Flash both boards with ESP-IDF v5.5.4 (read this first)

The vendored firmware and every result in `results/` were produced with **ESP-IDF v5.5.4**.

| Step | Where | Do this | Expect |
|---|---|---|---|
| 1 | your PC | `idf.py --version` | `ESP-IDF v5.5.4` |
| 2 | VS Code | Install the **ESP-IDF** extension → `ESP-IDF: Configure ESP-IDF Extension` → *Use existing setup* → select the **v5.5.4** installation | extension reports v5.5.4 |
| 3 | **DUT** window | `File → Open Folder…` → `firmware/iperf-esp32s3` · `Set Espressif Device Target` → `esp32s3` · select port `COM8` · **Build** → **Flash** | `Hash of data verified.` |
| 4 | **REF** window | same for `firmware/iperf-esp32c6`, target `esp32c6`, port `COM10` | `Hash of data verified.` |
| 5 | both | **Close the Monitor before flashing the other board** | port is not `PermissionError(13)` |

Command-line equivalent:

```bash
cd firmware/iperf-esp32s3 && idf.py set-target esp32s3 && idf.py -p COM8  flash && cd ../..
cd firmware/iperf-esp32c6 && idf.py set-target esp32c6 && idf.py -p COM10 flash && cd ../..
```

**Building with a different IDF version is the most common way this test fails before it starts.**
Example: v5.4.1 rewrites `dependencies.lock` (`version: 5.5.4` → `5.4.1`) and can hit Kconfig/API
mismatches — and even when it compiles, the numbers come from a different Wi-Fi driver/PHY, so they
are not comparable to the reference run. Clean up before rebuilding:

```bash
cd firmware/iperf-esp32s3
idf.py fullclean
del sdkconfig            # PowerShell;  rm sdkconfig on bash
git checkout -- dependencies.lock
```

## 2. Command reference — which board, what it does, what you should see

Rows are in run order: group **A** sets the radios up and scans, **B** is TCP, **C** is UDP,
**D** is housekeeping. The two terminals interleave — the `Board` column says which one you are in.

### A. Set up, scan, associate

| # | Board | Command | What it does | Expected output (key lines) | PASS / FAIL |
|---|---|---|---|---|---|
| A1 | **DUT** | `wifi_mode sta` | Puts the DUT radio in station mode so it can scan and connect. Both boards boot in STA, so this is a safety reset. | `wifi:mode : sta (10:bd:a3:09:48:48)`<br>`WIFI: DONE.SET_WIFI_MODE,OK.` | ✅ chip MAC printed **and** `DONE.SET_WIFI_MODE,OK.` |
| A2 | **DUT** | `scan` | Active scan of every channel: one line per AP (BSSID, SSID, RSSI, auth, channel) plus a total. Use `scan`, **not** `sta_scan` (the latter can panic the supplicant on some builds). | `WIFI: +SCAN:[42:84:b5:fb:88:0f][IOT][rssi=-48][auth=wpa2][ch=1]`<br>`WIFI: SCAN_DONE: Found 14 APs` | ✅ several APs with a sensible RSSI spread.<br>⚠️ 0–2 APs while a phone sees many = RF path problem.<br>`'scan' is deprecated` is a harmless warning. |
| A3 | **REF** | `wifi_mode sta` then `scan` | Same scan on the reference, at the same moment — this is the baseline the DUT is compared against. | `WIFI: SCAN_DONE: Found N APs` | ✅ compare the **same BSSIDs**; RSSI should be within a few dB (§3). |
| A4 | **REF** | `wifi_mode ap` | Turns REF into the SoftAP: it beacons and runs a DHCP server at `192.168.4.1`. No real network is involved. | `wifi:mode : softAP (40:4c:ca:55:31:8d)`<br>`esp_netif_lwip: DHCP server started on interface WIFI_AP_DEF with IP: 192.168.4.1`<br>`WIFI: WIFI_EVENT_AP_START` | ✅ all three lines present. |
| A5 | **REF** | `ap_set WIFI_HW_TEST test12345 -a wpa2 --disable_pmf` | Sets the SSID/password/auth mode and restarts the AP with them. `--disable_pmf` avoids a known crash in the STA supplicant. | `WIFI: WIFI_EVENT_AP_STOP`<br>`WIFI: WIFI_EVENT_AP_START`<br>`WIFI: DONE.SET_AP_CONFIG,OK.` | ✅ `DONE.SET_AP_CONFIG,OK.`<br>❌ `missing option <ssid>` / `Command returned non-zero error code` = typo. |
| A6 | **DUT** | `sta_connect WIFI_HW_TEST test12345` | DUT joins REF: scan → auth → assoc → DHCP. | `wifi:connected with WIFI_HW_TEST, aid = 1, channel 1, 40U, bssid = 40:4c:ca:55:31:8d`<br>`wifi:security: WPA2-PSK, phy: bgn, rssi: -24, cipher(pairwise:0x3, group:0x3), pmf:1`<br>`esp_netif_handlers: sta ip: 192.168.4.2, mask: 255.255.255.0, gw: 192.168.4.1` | ✅ `sta ip:` line = success — **write that address down, it is `<DUT-IP>`** used in B5/C4.<br>`reason: 201` + reconnect = normal first-attempt miss.<br>No output and a dead console = stale NVS (§4.4). |
| A7 | **DUT** | `ping 192.168.4.1` | ICMP to REF — proves the link carries packets and shows latency/loss. | `PING 192.168.4.1 (192.168.4.1) 64 data bytes`<br>`64 bytes from 192.168.4.1: icmp_seq=1 ttl=64 time=53 ms`<br>`--- 192.168.4.1 ping statistics ---` | ✅ ≥95 % of sequences answered, one line per reply; single-digit–tens of ms typical, a ~50 ms first reply is normal.<br>❌ every sequence times out = association is broken. |

### B. TCP throughput — both directions

| # | Board | Command | What it does | Expected output (key lines) | PASS / FAIL |
|---|---|---|---|---|---|
| B1 | **REF** | `iperf --abort` | Stops any iperf left over from an earlier run. A finished test leaves a **server** running, and a second instance is refused with `IPERF: iperf is already running.` | no output, prompt returns (or `iperf: iperf exit`) | ✅ do this on the board you are about to touch, before every new role. |
| B2 | **REF** | `iperf -s -i 2` | REF listens for a TCP client on port 5001 and prints a report every 2 s (default run time 30 s). | `IPERF: mode=tcp-server sip=localhost:5001, dip=0.0.0.0:5001, interval=2, time=30`<br>`iperf: Socket created`<br>`iperf: Socket bound, port 5001` | ✅ REF then prints `accept: 192.168.4.2` when the DUT connects. |
| B3 | **DUT** | `iperf --abort` then `iperf -c 192.168.4.1 -i 2 -t 15` | DUT → REF TCP traffic for 15 s: per-interval bandwidth plus a final summary. | `IPERF: mode=tcp-client sip=localhost:5001, dip=192.168.4.1:5001, interval=2, time=15`<br>`iperf: Successfully connected`<br>`Interval       Bandwidth`<br>` 0.0- 2.0 sec  25.56 Mbits/sec`<br>` 0.0-15.0 sec  26.45 Mbits/sec` | ✅ report the **`0.0-15.0 sec`** summary — this is the DUT's **TCP TX**.<br>⚠️ `0.00 Mbits/sec` + `errno 118 host is unreachable` = not associated → redo A6. |
| B4 | **DUT** | `iperf --abort` then `iperf -s -i 2` | Swaps roles: the DUT becomes the server. | as B2 (in the DUT terminal) | ✅ `mode=tcp-server` and `Socket bound, port 5001`. |
| B5 | **REF** | `iperf --abort` then `iperf -c <DUT-IP> -i 2 -t 15` | REF → DUT TCP traffic; the DUT only receives. | as B3 (REF prints it) | ✅ the final summary is the DUT's **TCP RX**.<br>⚠️ Never hand-copy a `0.0-30.0 sec` line from the *next* test — that is a leftover server from a previous run; abort it (B1). |

### C. UDP throughput — both directions

**Read this before judging any UDP number.** In `iperf -c <ip> -u -b 40`, `-b` is the **offered load** —
the rate iperf is asked to send at, in Mbit/s (`-b 40`, never `40M`: that gives
`invalid argument "40M" to option -b`). iperf can never report more than it was asked to send, so a low
offer **caps the measurement**: an ESP32-S3 that can do 30 Mbit/s still reports ~20 at `-b 20` and looks
slow for no reason. iperf's own help says it plainly —
`-b, --bandwidth=<bandwidth>  bandwidth to send at in Mbits/sec`.

| Offered `-b` | Achieved | What it tells you |
|---|---|---|
| 20 | below 20 | the **offer** set the number — says nothing about the link |
| 30 | below 30 | same, but directly comparable with the documented 30 Mbit/s air figure |
| **40** | plateaus | the **link** is the limit — this is the number to report |

So the main UDP rows below run at **`-b 40`**, above the documented air figure (30 Mbit/s), and the
sweep **20 / 30 / 40** shows where the achieved value stops following the offer — that plateau is the
link ceiling. Measured on the Astra board, changing only the offer:

| Offered `-b` | Achieved | Looks like |
|---|---|---|
| 20 | 18.53 Mbit/s | a radio that cannot reach the documented 30 |
| 30 | 24.99 Mbit/s | still climbing |
| **40** | **33.15 Mbit/s** | the real link — **above** the documented 30 |

| # | Board | Command | What it does | Expected output (key lines) | PASS / FAIL |
|---|---|---|---|---|---|
| C1 | **REF** | `iperf --abort` then `iperf -s -u -i 2` | REF listens for UDP and additionally prints loss/jitter per interval. | `IPERF: mode=udp-server sip=0.0.0.0:5001, dip=0.0.0.0:5001, interval=2`<br>`iperf: Socket bound, port 5001` | ✅ `mode=udp-server` (the bound port may differ from 5001). |
| C2 | **DUT** | `iperf --abort` then `iperf -c 192.168.4.1 -u -b 40 -i 2 -t 10` | DUT → REF UDP at an **offered** 40 Mbit/s for 10 s (above the 30 Mbit/s air figure, so the link is the limit rather than the offer). | `IPERF: mode=udp-client sip=localhost:5001, dip=192.168.4.1:5001, interval=2, time=10`<br>` 0.0- 2.0 sec  33.51 Mbits/sec`<br>` 2.0- 4.0 sec  30.91 Mbits/sec`<br>` 0.0-10.0 sec  31.45 Mbits/sec` | ✅ report the final summary — DUT's **UDP TX** — **together with the offer** (`31.45 Mbit/s at -b 40`), and read **REF's** console for loss/jitter.<br>Repeat at `-b 20` and `-b 30`; if the achieved value tracks the offer, raise `-b` until it plateaus. |
| C3 | **DUT** | `iperf --abort` then `iperf -s -u -i 2` | Swaps roles: the DUT becomes the UDP server. | as C1 (in the DUT terminal) | ✅ |
| C4 | **REF** | `iperf --abort` then `iperf -c <DUT-IP> -u -b 40 -i 2 -t 10` | REF → DUT UDP; the DUT only receives (and reports loss). | as C2 (REF prints the client side; the DUT's line landed at `24.50 Mbits/sec`) | ✅ final summary = DUT's **UDP RX** at that offer; read the DUT console for loss. |

### D. Housekeeping and other console commands

| # | Board | Command | What it does | Expected output |
|---|---|---|---|---|
| D1 | either | `iperf --abort` | Stops iperf and frees the socket — do it before every new role. | no output / `iperf: iperf exit` |
| D2 | either | `help` | Lists every console command with its options. | the command list |
| D3 | either | `wifi_mode` | Queries the current mode (no argument = query). | `WIFI: mode: sta` or `WIFI: mode: ap` |
| D4 | either | `wifi_config_query` | Dumps the SSID/password/auth currently stored for the station. | the stored config |

**Reading the throughput numbers:** if TX ≈ RX and both are low, the limit is the peer or socket
buffers, not your board. If RSSI is clearly worse than REF, suspect antenna/matching. If anything
crashes in Wi-Fi/supplicant code, that is firmware, not RF.

## 3. Pass / fail — judged against the MCU documentation

Compare in this order:

**(a) Your reference board** — the primary evidence. Both boards scanned the same APs at the same
moment, so the DUT should match on AP count and RSSI and be in the same throughput class.

**(b) Espressif's published figures for the chip** — *"according to the MCU documentation"*, reference
only (**air / shield-box**, Mbit/s):

| Chip | UDP RX | UDP TX | TCP RX | TCP TX |
|---|---|---|---|---|
| **ESP32-S3** | 30 / 88 | 30 / 98 | 20 / 73 | 20 / 83 |
| **ESP32-C6** | 30 / 63 | 30 / 51 | 20 / 46 | 20 / 43 |
| ESP32-C3 | 30 / 50 | 30 / 40 | 20 / 35 | 20 / 37 |
| ESP32-S2 | 30 / 70 | 30 / 50 | 20 / 32 | 20 / 37 |
| ESP32 | 30 / 85 | 30 / 75 | 20 / 65 | 20 / 75 |
| ESP32-C2 | not published | | | |

**air** = best over-the-air result in Espressif's lab (ESP ↔ router, one stream); **shield-box** =
shielded box with an ASUS RT-N66U router. For an ESP ↔ ESP SoftAP test at ~0.5 m the **air** column
is the right yardstick; the shield-box column needs a router peer and clean RF and is not reachable
in an office. Full data + IDF commit stamps (S3: 2021-04-21, C6: 2024-12-14 — both older than
v5.5.4): [`../config/official_throughput.json`](../config/official_throughput.json).

| Check | Where it comes from | PASS when |
|---|---|---|
| scan vs REF | A2 / A3 | DUT APs ≥ 60 % of REF **and** median RSSI delta ≤ 8 dB (≤ 20 dB if REF has a better antenna) |
| association | A6 | IP obtained, link RSSI ≥ −70 dBm |
| ping | A7 | loss ≤ 5 % |
| TCP TX / RX | B3 / B5 | ≥ 15 Mbit/s (ESP32-S3 floor — the docs' air figure is 20 Mbit/s) |
| UDP TX / RX | C2 / C4 | ≥ 12 Mbit/s **at the offered `-b 40`** — always quote the offer with the number; docs' air figure is 30 Mbit/s |
| no panic | all | no `Guru Meditation` in either console |

**Reference run** (ESP32-S3 custom board ↔ ESP32-C6-DevKitC-1, IDF v5.5.4, ~0.5 m, chip antenna):

| Test | Measured | Official air (S3) | Verdict |
|---|---|---|---|
| scan | 14 APs vs 15 on the reference, median RSSI delta −9.0 dB | — | ✅ |
| association | `192.168.4.2`, RSSI −24 dBm | — | ✅ |
| ping | 5/5 replies, avg 26 ms | — | ✅ |
| TCP TX | 26.45 Mbit/s | 20 | ✅ above |
| TCP RX | 20.16 Mbit/s | 20 | ✅ consistent |
| UDP TX **at `-b 40`** | 31.45 Mbit/s | 30 | ✅ above |
| UDP RX **at `-b 40`** | 24.50 Mbit/s | 30 | ✅ passes the floor; ~82 % of the air figure |

All seven verdicts PASS: [`../results/20260911-152939_astra-s3-vs-c6-devkit.md`](../results/20260911-152939_astra-s3-vs-c6-devkit.md).
The earlier run in `results/20260911-142044_…md` measured UDP at `-b 20` (17.6 / 17.4 Mbit/s) and
looked like a failure against the 30 Mbit/s figure — the same board reaches 31–33 Mbit/s at `-b 40`.
That difference is the whole reason for the `-b` rule in §C.

## 4. Traps

1. **Wrong ESP-IDF version** → §1 (v5.5.4). Symptom: build/Kconfig errors, or `dependencies.lock`
   rewritten to another version.
2. **DTR/RTS**: a terminal that asserts them holds the chip in reset (boards wired `RTS→EN`,
   `DTR→BOOT0`) — no output at all. Release both when opening the port.
3. **Busy port**: close VS Code Monitor / `idf.py monitor` before flashing.
4. **`sta_connect` hangs with no events** → stale NVS:
   `esptool.py --chip esp32s3 -p COM8 erase_region 0x9000 0x6000`, then retry.
5. **`-b` takes a bare number** (`-b 40`, not `40M`) **and it is the offered load** — offer less than
   the link can carry and the result is capped by your own offer (§C). Always `iperf --abort` before a
   new role.
6. **Same room, same time** — RSSI and throughput are only comparable within one session.

## 5. Full captured output of the key commands

<details><summary><code>scan</code> (DUT, A2)</summary>

```
> scan
WIFI: 'scan' is deprecated, please use 'sta_scan'.
WIFI: DONE.STA_SCAN_START,OK.
WIFI: +SCAN:[42:84:b5:fb:88:0f][IOT][rssi=-48][auth=wpa2][ch=1]
WIFI: +SCAN:[42:84:b5:fb:88:11][IOT_Guest][rssi=-48][auth=wpa2][ch=1]
WIFI: +SCAN:[8c:de:f9:33:97:f8][OpenVp_2.4G][rssi=-49][auth=wpa2][ch=1]
WIFI: +SCAN:[14:84:73:40:0f:a1][HaycoAP2][rssi=-72][auth=wpa2_enterprise][ch=6]
WIFI: +SCAN:[b6:e4:f6:6d:6a:f4][nova 13][rssi=-71][auth=wpa2][ch=9]
WIFI: +SCAN:[a2:69:38:82:f7:c2][IOT_Wi-Fi5][rssi=-90][auth=wpa2][ch=1]
WIFI: SCAN_DONE: Found 14 APs
```

</details>

<details><summary><code>sta_connect</code> (DUT, A6)</summary>

```
> sta_connect WIFI_HW_TEST test12345
WIFI: Connecting to WIFI_HW_TEST...
WIFI: DONE.WIFI_CONNECT_START,OK.
wifi:connected with WIFI_HW_TEST, aid = 1, channel 1, 40U, bssid = 40:4c:ca:55:31:8d
wifi:security: WPA2-PSK, phy: bgn, rssi: -24, cipher(pairwise:0x3, group:0x3), pmf:1
WIFI: WIFI_EVENT_STA_CONNECTED!
WIFI: STA_CONNECTED_AUTH:3
esp_netif_handlers: sta ip: 192.168.4.2, mask: 255.255.255.0, gw: 192.168.4.1
```

</details>

<details><summary><code>iperf -c</code> TCP client (DUT, B3) and UDP client (DUT, C2 — the <code>-b 40</code> run)</summary>

```
> iperf -c 192.168.4.1 -i 2 -t 15
IPERF: mode=tcp-client sip=localhost:5001, dip=192.168.4.1:5001, interval=2, time=15
iperf: Successfully connected
Interval       Bandwidth
 0.0- 2.0 sec  25.56 Mbits/sec
 2.0- 4.0 sec  25.69 Mbits/sec
 4.0- 6.0 sec  25.25 Mbits/sec
 6.0- 8.0 sec  26.25 Mbits/sec
 8.0-10.0 sec  28.06 Mbits/sec
 0.0-15.0 sec  26.45 Mbits/sec

> iperf -c 192.168.4.1 -u -b 40 -i 2 -t 10
IPERF: mode=udp-client sip=localhost:5001, dip=192.168.4.1:5001, interval=2, time=10
 0.0- 2.0 sec  33.51 Mbits/sec
 2.0- 4.0 sec  30.91 Mbits/sec
 4.0- 6.0 sec  33.35 Mbits/sec
 6.0- 8.0 sec  29.52 Mbits/sec
 8.0-10.0 sec  29.94 Mbits/sec
 0.0-10.0 sec  31.45 Mbits/sec
```

The same DUT, the same 10 s run, only `-b` changed: **18.53 Mbit/s at `-b 20`**, 24.99 at `-b 30`,
**31.45 at `-b 40`**. Offer less than the link can carry and you measure your own offer.

</details>
