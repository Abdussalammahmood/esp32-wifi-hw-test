# Test firmware (source included)

Both boards run the **unmodified ESP-IDF `wifi/iperf` example**. Its console provides every command
the harness drives: `wifi_mode`, `ap_set`, `sta_connect`, `scan`, `ping`, `iperf`.

Two ready-to-build copies are vendored here, each with its `managed_components/` and
`dependencies.lock`, so the first build works **offline** (no component-registry download):

| Folder | Flash to | Target |
|---|---|---|
| `firmware/iperf-esp32s3/` | the **DUT** (board under test) | `esp32s3` |
| `firmware/iperf-esp32c6/` | the **REF** (known-good reference) | `esp32c6` |

## Build and flash

In an ESP-IDF terminal (adjust the port):

```bash
cd firmware/iperf-esp32s3
idf.py set-target esp32s3        # once, or after deleting sdkconfig
idf.py build
idf.py -p COM8 flash             # DUT port

cd ../iperf-esp32c6
idf.py set-target esp32c6
idf.py build
idf.py -p COM10 flash            # REF port
```

Console baud is **115200** (the example's default) — the harness assumes it.

## Different chips?

Generate a copy for any target from your own ESP-IDF checkout:

```bash
python tools/prepare_firmware.py --target esp32c3 --dest firmware/iperf-esp32c3 \
  --idf-path <IDF_PATH> \
  --tools-path <IDF_TOOLS_PATH> \
  --python-env <IDF_VENV> \
  --path-prepend <IDF_TOOLS_PATH>/tools/riscv32-esp-elf/<ver>/riscv32-esp-elf/bin \
  --path-prepend <IDF_TOOLS_PATH>/tools/cmake/<ver>/bin \
  --path-prepend <IDF_TOOLS_PATH>/tools/ninja/<ver> \
  --build
```

(`--path-prepend` for `xtensa-esp-elf` instead of `riscv32-esp-elf` on Xtensa targets.)

## Notes

- Nothing here is modified: no product code, no custom hardware config. That is what makes the test
  firmware-agnostic — if the numbers are bad, it is hardware, not an application bug.
- `build/`, `sdkconfig` and `sdkconfig.old` are git-ignored; the `sdkconfig.defaults*` per-target
  files that the example ships are committed and selected automatically by `idf.py set-target`.
- The example creates its own SoftAP at `192.168.4.1`, so **no real network credentials** are needed
  by the harness, the config, or these firmware projects.
- Full step-by-step manual procedure (with expected output and the acceptance table):
  [`../docs/MANUAL_TESTING.md`](../docs/MANUAL_TESTING.md).
