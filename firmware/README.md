# Test firmware for the harness

Both boards must run the **unmodified ESP-IDF `wifi/iperf` example**. Its console provides the
commands the harness drives: `wifi_mode`, `ap_set`, `sta_connect`, `scan`, `ping`, `iperf`.

## Build it for each board

Pick the toolchain paths for your machine. Nothing below has to live in this repo — only the
`.bin` output matters, and the harness only talks to the board over UART.

With the helper (copies the example out of the IDF tree, then optionally builds):

```bash
python tools/prepare_firmware.py \
  --target esp32s3 \
  --dest firmware/iperf-esp32s3 \
  --idf-path D:/IDF_5_5_AI/.espressif/v5.5.4/esp-idf \
  --tools-path C:/Espressif \
  --python-env C:/Espressif/tools/python/v5.5.4/venv \
  --path-prepend C:/Espressif/tools/xtensa-esp-elf/esp-14.2.0_20260121/xtensa-esp-elf/bin \
  --path-prepend C:/Espressif/tools/cmake/3.30.2/bin \
  --path-prepend C:/Espressif/tools/ninja/1.12.1 \
  --build
```

Repeat with `--target esp32c6` (and the `riscv32-esp-elf` bin dir) for a C6 reference board.

Or build it by hand in an **ESP-IDF PowerShell/terminal**:

```powershell
cp -r $env:IDF_PATH/examples/wifi/iperf C:\work\iperf-esp32s3
cd C:\work\iperf-esp32s3
idf.py set-target esp32s3
idf.py -p COM8 flash
```

Then flash the second copy to the other board with its own port.

## Notes

- **Console baud rate is 115200** by default for this example — keep it, the harness assumes it.
- The example creates its own SoftAP at `192.168.4.1`, so **no real network credentials** are needed
  anywhere in this repo or in the config.
- `firmware/*/build` and `firmware/*/managed_components` are git-ignored on purpose; build outputs do
  not belong in the repo.
- If the example's default sdkconfig disagrees with your module's flash size, that is cosmetic — the
  boot log will warn and continue.
