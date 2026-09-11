# Manual test — no harness, no Python

Run exactly what `wifi_hw_test.py` automates, using two serial terminals at **115200**.
**DUT** = board under test · **REF** = known-good reference board.

> Prefer automation? The **[`execute-wifi-hw-test`](../.claude/skills/execute-wifi-hw-test/SKILL.md)
> AI skill** (or the harness) does all of this for you — see [`../README.md`](../README.md).

The console prompt is `iperf>`. Lines are prefixed by the log level and a timestamp
(`I (1234) WIFI: …`) — the timestamp is omitted below.

---

## 1. Use ESP-IDF v5.5.4 (read this first)

The vendored firmware and the results in `results/` were produced with **ESP-IDF v5.5.4**.
Check yours:

```bash
idf.py --version          # expect: ESP-IDF v5.5.4
```

**Building with a different IDF version is the most common way this test fails before it starts.**
Example: building with v5.4.1 rewrites `dependencies.lock` (`version: 5.5.4` → `5.4.1`) and can hit
Kconfig/API mismatches — and even when it compiles, the Wi-Fi numbers come from a different
driver/PHY, so they are not comparable to the reference run. Clean up before rebuilding:

```bash
cd firmware/iperf-esp32s3
idf.py fullclean
del sdkconfig            # PowerShell;  rm sdkconfig on bash
git checkout -- dependencies.lock
```

### Build & flash with VS Code (ESP-IDF extension)

1. Install the **ESP-IDF** extension → `ESP-IDF: Configure ESP-IDF Extension` → *Use existing setup*
   → select the **v5.5.4** installation.
2. `File → Open Folder…` → `firmware/iperf-esp32s3` (second window for the other board).
3. `ESP-IDF: Set Espressif Device Target` → the chip (`esp32s3`, `esp32c6`, …).
4. Bottom bar: pick the COM port → **Build** → **Flash** (or `ESP-IDF: Build, Flash and Monitor`).
5. **Close the Monitor before flashing the next board** — a monitor holds the port
   (`PermissionError(13)`).

Command line equivalent:

```bash
cd firmware/iperf-esp32s3 && idf.py set-target esp32s3 && idf.py -p COM8  flash && cd ../..
cd firmware/iperf-esp32c6 && idf.py set-target esp32c6 && idf.py -p COM10 flash && cd ../..
```

## 2. Commands, what they do, and what you should see

### 2.1 `wifi_mode sta` — put the board in station mode

*Does:* sets the Wi-Fi mode to station so the board can scan and connect to an AP. Both boards start
in STA by default, so this is mostly a safety reset.

```
> wifi_mode sta
WIFI: mode: sta
wifi:mode : sta (10:bd:a3:09:48:48)
WIFI: DONE.SET_WIFI_MODE,OK.
```
✅ Expect the chip MAC in `wifi:mode : sta (…)` and `DONE.SET_WIFI_MODE,OK.`

### 2.2 `scan` — list nearby APs (RX / antenna check)

*Does:* active scan of every channel; prints one line per AP (BSSID, SSID, RSSI, auth, channel) and
then the total. Use `scan`, **not** `sta_scan` (the latter can panic the supplicant on some builds).

```
> scan
WIFI: 'scan' is deprecated, please use 'sta_scan'.      <- harmless warning
WIFI: DONE.STA_SCAN_START,OK.
WIFI: +SCAN:[8c:de:f9:33:97:f8][OpenVp_2.4G][rssi=-47][auth=wpa2][ch=1]
WIFI: +SCAN:[42:84:b5:fb:88:0f][IOT][rssi=-61][auth=wpa2][ch=1]
WIFI: +SCAN:[14:84:73:40:0f:a1][HaycoAP2][rssi=-77][auth=wpa2_enterprise][ch=6]
WIFI: +SCAN:[b6:e4:f6:6d:6a:f4][nova 13][rssi=-83][auth=wpa2][ch=9]
WIFI: SCAN_DONE: Found 18 APs
```
✅ Several APs with a sensible RSSI spread. ⚠️ 0–2 APs while a phone sees many = RF path problem.
Do this on **both** boards and compare the same BSSIDs (RSSI should be within a few dB; see §3).

### 2.3 `wifi_mode ap` — make the REFERENCE board an access point

*Does:* switches the reference board to SoftAP; it starts beaconing and runs a DHCP server at
192.168.4.1. The DUT will connect to it, so **no real network is involved**.

```
> wifi_mode ap
WIFI: mode: ap
wifi:mode : softAP (40:4c:ca:55:31:8d)
wifi:Init max length of beacon: 752/752
esp_netif_lwip: DHCP server started on interface WIFI_AP_DEF with IP: 192.168.4.1
WIFI: WIFI_EVENT_AP_START
WIFI: DONE.SET_WIFI_MODE,OK.
```
✅ Expect `softAP (…)`, the DHCP line with **192.168.4.1** and `WIFI_EVENT_AP_START`.

### 2.4 `ap_set WIFI_HW_TEST test12345 -a wpa2 --disable_pmf` — configure the AP

*Does:* sets SSID/password/auth mode and restarts the AP with that config.
(`-a open` for an open AP — then omit the password. `--disable_pmf` avoids a known crash in the STA
supplicant.)

```
> ap_set WIFI_HW_TEST test12345 -a wpa2 --disable_pmf
WIFI: WIFI_EVENT_AP_STOP
wifi:Disabled PMF config for SoftAP
WIFI: WIFI_EVENT_AP_START
WIFI: DONE.SET_AP_CONFIG,OK.
```
✅ Expect `DONE.SET_AP_CONFIG,OK.` and AP_STOP → AP_START. ❌ `missing option <ssid>` or
`Command returned non-zero error code` = typo in the command.

### 2.5 `sta_connect WIFI_HW_TEST test12345` — DUT joins the reference

*Does:* stores the SSID/password for the station and starts the connection (scan → auth → assoc →
DHCP).

```
> sta_connect WIFI_HW_TEST test12345
WIFI: Connecting to WIFI_HW_TEST...
WIFI: DONE.WIFI_CONNECT_START,OK.
wifi:connected with WIFI_HW_TEST, aid = 1, channel 1, 40U, bssid = 40:4c:ca:55:31:8d
wifi:security: WPA2-PSK, phy: bgn, rssi: -32, cipher(pairwise:0x3, group:0x3), pmf:1
WIFI: WIFI_EVENT_STA_CONNECTED!
WIFI: STA_CONNECTED_AUTH:3
esp_netif_handlers: sta ip: 192.168.4.2, mask: 255.255.255.0, gw: 192.168.4.1
```
✅ `sta ip: 192.168.4.X` = success. **Note the IP** — it is the `<DUT-IP>` used below — and the
`rssi:` value (at ~0.5 m expect better than −50 dBm).

- `WIFI_EVENT_STA_DISCONNECTED! reason: 201` followed by a reconnect is normal: the first attempt
  found no AP (NO_AP_FOUND) and the helper retried.
- No output at all and the console stops answering → **stale NVS**:
  `esptool.py --chip esp32s3 -p COM8 erase_region 0x9000 0x6000`, then retry.

### 2.6 `ping 192.168.4.1` — latency / loss to the AP

```
> ping 192.168.4.1
PING 192.168.4.1 (192.168.4.1) 64 data bytes
64 bytes from 192.168.4.1: icmp_seq=1 ttl=64 time=331 ms
64 bytes from 192.168.4.1: icmp_seq=2 ttl=64 time=7 ms
64 bytes from 192.168.4.1: icmp_seq=3 ttl=64 time=59 ms
--- 192.168.4.1 ping statistics ---
```
✅ All (or ≥95 %) sequences answered, one line per reply. Typical times are single-digit to tens of
ms; an occasional 300 ms outlier happens. ❌ timeout for every sequence = association is broken.

### 2.7 `iperf --abort` — stop any running iperf

*Does:* aborts the iperf instance on that board. The example keeps a **server running** after a test,
and a second instance is refused with `IPERF: iperf is already running.` — so abort before every new
role.

```
> iperf --abort
(no output, or: iperf: iperf exit)
```
✅ Prompt returns. Always do this on **both** boards between test roles.

### 2.8 `iperf -s …` — start a server (TCP or UDP)

*Does:* listens for a client on port 5001. `-i 2` prints a report every 2 s; the default run time is
30 s.

```
> iperf -s -i 2
IPERF: mode=tcp-server sip=localhost:5001, dip=0.0.0.0:5001, interval=2, time=30
iperf: Socket created
iperf: Socket bound, port 5001          <- for UDP the bound port may differ
```
✅ `mode=tcp-server` (or `udp-server` with `-u`) and `Socket created`; the client side then prints
`accept: <ip>`.

### 2.9 `iperf -c <IP> …` — run the client and read the numbers

*Does:* sends traffic for `-t` seconds and prints per-interval bandwidth plus a final summary line.

```
> iperf -c 192.168.4.1 -i 2 -t 15          # TCP, DUT -> REF
IPERF: mode=tcp-client sip=localhost:5001, dip=192.168.4.1:5001, interval=2, time=15
iperf: Successfully connected
Interval       Bandwidth
 0.0- 2.0 sec  27.31 Mbits/sec
 2.0- 4.0 sec  21.38 Mbits/sec
…
 0.0-15.0 sec  20.35 Mbits/sec              <- the number you report
```
UDP is the same with `-u -b 20` (`-b` is a **number in Mbit/s** — `20M` is rejected with
`invalid argument "20M" to option -b`):
```
> iperf -c 192.168.4.1 -u -b 20 -i 2 -t 10
IPERF: mode=udp-client … time=10
Interval       Bandwidth
 0.0- 2.0 sec  18.52 Mbits/sec
 0.0-10.0 sec  17.51 Mbits/sec
```
✅ `Successfully connected`, steady per-interval numbers, final summary near the offered rate.
For UDP also read the **server** console: it reports loss/jitter for the same run.
⚠️ `0.00 Mbits/sec` with `errno 118 host is unreachable` = the DUT is not associated; redo §2.5.
⚠️ Don't hand-copy a `0.0-30.0 sec` summary that appears in the *next* test — that is a leftover
server from the previous run; abort it (§2.7).

### 2.10 The whole sequence, copy-paste

```
DUT: wifi_mode sta
DUT: scan                                     # note AP count + RSSI
REF: wifi_mode sta
REF: scan                                     # compare with the DUT

REF: wifi_mode ap
REF: ap_set WIFI_HW_TEST test12345 -a wpa2 --disable_pmf

DUT: sta_connect WIFI_HW_TEST test12345       # note <DUT-IP> and rssi
DUT: ping 192.168.4.1

# TCP  DUT -> REF , then REF -> DUT
REF: iperf --abort
REF: iperf -s -i 2
DUT: iperf --abort
DUT: iperf -c 192.168.4.1 -i 2 -t 15
DUT: iperf --abort
DUT: iperf -s -i 2
REF: iperf --abort
REF: iperf -c <DUT-IP> -i 2 -t 15

# UDP  DUT -> REF , then REF -> DUT   (+ repeat with -b 30 for the load sweep)
REF: iperf --abort
REF: iperf -s -u -i 2
DUT: iperf --abort
DUT: iperf -c 192.168.4.1 -u -b 20 -i 2 -t 10
DUT: iperf --abort
DUT: iperf -s -u -i 2
REF: iperf --abort
REF: iperf -c <DUT-IP> -u -b 20 -i 2 -t 10
```

Other useful console commands: `help` (list commands), `wifi_mode` (query current mode),
`wifi_config_query` (dump current config), `iperf --abort` (stop iperf).

## 3. Pass / fail — judged against the MCU documentation

Compare in this order:

**(a) Your reference board** — the primary evidence. Both boards scanned the same APs at the same
moment, so the DUT should match on AP count and RSSI and be in the same throughput class.

**(b) Espressif's published figures for the chip** — *"according to the MCU documentation"*, reference only
(**air / shield-box**, Mbit/s):

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
in an office. Full data + IDF commit stamps (S3: 2021-04-21, C6: 2024-12-14 — older than v5.5.4):
[`../config/official_throughput.json`](../config/official_throughput.json).

| Check | PASS when |
|---|---|
| scan vs REF | DUT APs ≥ 60 % of REF **and** median RSSI delta ≤ 8 dB (≤ 20 dB if REF has a better antenna) |
| association | IP obtained, link RSSI ≥ −70 dBm |
| ping | loss ≤ 5 % |
| TCP TX / RX | ≥ 15 Mbit/s (ESP32-S3 floor — the docs' air figure is 20 Mbit/s) |
| UDP TX / RX | ≥ 12 Mbit/s **at the offered rate** — docs' air figure is 30 Mbit/s, so repeat with `-b 30` |
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
firmware, not RF.

## 4. Traps

1. **Wrong ESP-IDF version** → see §1 (v5.5.4). Symptom: build/Kconfig errors, or `dependencies.lock`
   rewritten to another version.
2. **DTR/RTS**: a terminal that asserts them holds the chip in reset (boards wired `RTS→EN`,
   `DTR→BOOT0`) — no output at all. Release both when opening the port.
3. **Busy port**: close VS Code Monitor / `idf.py monitor` before flashing.
4. **`sta_connect` hangs with no events** → stale NVS:
   `esptool.py --chip esp32s3 -p COM8 erase_region 0x9000 0x6000`, then retry.
5. **`-b` wants a number** (`-b 20`, not `20M`); always `iperf --abort` before a new role.
6. **Same room, same time** — RSSI and throughput are only comparable within one session.
