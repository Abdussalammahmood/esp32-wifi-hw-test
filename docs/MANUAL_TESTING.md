# Manual testing (no harness, no AI)

Everything the harness automates can be done by hand with two serial terminals. Use this if you want
to build/flash yourself, or to double-check what the harness claims.

Boards: **DUT** = board under test · **REF** = known-good reference board.

---

## 1. Get the firmware onto both boards

The firmware is the stock ESP-IDF `wifi/iperf` example, vendored in this repo so you don't need to
hunt for it:

```
firmware/iperf-esp32s3/     <- build & flash to the DUT     (target esp32s3)
firmware/iperf-esp32c6/     <- build & flash to the REF     (target esp32c6)
```

Both folders already contain their `managed_components/` and `dependencies.lock`, so the first build
works offline.

Add a second target yourself if your boards are different chips:

```bash
python tools/prepare_firmware.py --target esp32c3 --dest firmware/iperf-esp32c3 \
    --idf-path <IDF_PATH> --tools-path <IDF_TOOLS_PATH> --python-env <IDF_VENV> \
    --path-prepend <toolchain>/bin --path-prepend <cmake>/bin --path-prepend <ninja> --build
```

### Build + flash (ESP-IDF terminal)

```bash
cd firmware/iperf-esp32s3
idf.py set-target esp32s3        # only needed once / after deleting sdkconfig
idf.py build
idf.py -p COM8 flash             # <- your DUT port

cd ../iperf-esp32c6
idf.py set-target esp32c6
idf.py build
idf.py -p COM10 flash            # <- your REF port
```

Keep the **console baud rate at 115200** (the example's default).

### Serial terminal gotchas

- Open the port with **DTR and RTS released**. On boards that wire the debug header straight to the
  chip (`RTS→EN`, `DTR→BOOT0`), asserting those lines holds the chip in reset or parks it in download
  mode and you'll see no output at all.
- One program per port: close VS Code's Monitor / `idf.py monitor` before flashing, or you get
  `PermissionError(13)`.
- If `sta_connect` hangs (no connect events, console stops answering), the board has a stale WiFi
  config in NVS. Clear it and retry:
  ```bash
  esptool.py --chip esp32s3 -p COM8 erase_region 0x9000 0x6000
  ```

---

## 2. Run the test by hand

Two terminals open at 115200 (one per board). Type into them; the example prints an `iperf>` prompt.

### Step 1 — scan on the DUT (RX / antenna check)

```
DUT: wifi_mode sta
DUT: scan
```
Note the `SCAN_DONE: Found N APs` line and the RSSI of a few APs you also see on a phone.

Then do the same on the REF (it is still a station at this point):
```
REF: wifi_mode sta
REF: scan
```

Compare: the DUT should see **roughly the same APs** as the REF, and the RSSI of the same BSSID
should be within a few dB. A big deficit (≥ 10 dB) or many missing APs points at the RF path
(antenna, matching, FEM, damaged module).

### Step 2 — REF becomes the access point

```
REF: wifi_mode ap
REF: ap_set WIFI_HW_TEST test12345 -a wpa2 --disable_pmf
```
(`--disable_pmf` avoids a known supplicant crash on some builds. No real network is involved — the
reference board *is* the AP at 192.168.4.1.)

### Step 3 — DUT associates

```
DUT: sta_connect WIFI_HW_TEST test12345
```
Look for `connected with WIFI_HW_TEST ... channel N` and `sta ip: 192.168.4.X`, plus an
`rssi: -NN` line. At ~0.5 m expect better than −50 dBm.

### Step 4 — ping (latency / loss)

```
DUT: ping 192.168.4.1
```

### Step 5 — TCP throughput, both directions

```
# DUT -> REF  (DUT transmit path)
REF: iperf --abort
REF: iperf -s -i 2
DUT: iperf -c 192.168.4.1 -i 2 -t 15

# REF -> DUT  (DUT receive path)
DUT: iperf --abort
DUT: iperf -s -i 2
REF: iperf --abort
REF: iperf -c 192.168.4.X -i 2 -t 15        # X from step 3
```

### Step 6 — UDP throughput, both directions

```
# DUT -> REF
REF: iperf --abort
REF: iperf -s -u -i 2
DUT: iperf --abort
DUT: iperf -c 192.168.4.1 -u -b 20 -i 2 -t 10

# REF -> DUT
DUT: iperf --abort
DUT: iperf -s -u -i 2
REF: iperf --abort
REF: iperf -c 192.168.4.X -u -b 20 -i 2 -t 10
```

`-b` takes a plain number in Mbit/s — `-b 20M` is rejected with
`invalid argument "20M" to option -b`.

Always `iperf --abort` before starting a new iperf role: the example keeps a server running, and a
second instance is refused with `iperf is already running`.

---

## 3. Judge the result yourself

Same rules the harness applies (`config/thresholds.json`):

| Check | PASS when |
|---|---|
| scan vs reference | DUT AP count ≥ 60 % of REF's **and** median RSSI delta on common BSSIDs ≤ 8 dB (relax to ≤ 20 dB when the REF has a different, better antenna) |
| association | an IP is obtained and link RSSI ≥ −70 dBm |
| ping | packet loss ≤ 5 % |
| TCP TX / RX (ESP32-S3) | ≥ 15 Mbit/s averaged over the run |
| UDP TX / RX (ESP32-S3) | ≥ 12 Mbit/s |
| other chips | see `config/thresholds.json` |
| no panic | no `Guru Meditation` in either console |

Reference numbers from a real run (ESP32-S3 custom board ↔ ESP32-C6-DevKitC-1, ~0.5 m, RSSI −32 dBm):

| Test | Result |
|---|---|
| scan | 15 APs vs 14 reference, median RSSI delta −11 dB (chip antenna vs devkit antenna) |
| association | 192.168.4.2 @ −32 dBm |
| ping | 5/5, avg 65 ms |
| TCP TX / RX | 24.2 / 20.5 Mbit/s |
| UDP TX / RX | 17.6 / 17.4 Mbit/s |

Interpretation shortcuts:

- **TXTX ≈ RX and everything above the floors** → WiFi hardware is healthy.
- **Low absolute Mbit/s but the reference is the same class** → the peer, socket buffers or channel
  congestion is the limit, not your board.
- **RSSI clearly worse than the reference** → antenna/matching problem.
- **Crashes in the WiFi/supplicant code** → firmware/library issue, not RF.

Full write-up of the run: `results/20260911-142044_astra-s3-vs-c6-devkit.md`.
