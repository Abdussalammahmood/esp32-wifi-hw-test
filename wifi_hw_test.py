#!/usr/bin/env python3
"""Generic ESP32 WiFi hardware verification harness.

Tests a *device under test* (DUT) board against a *reference* board using the ESP-IDF
`wifi/iperf` example console, then writes a markdown report with PASS/FAIL verdicts.

  reference board  : becomes a SoftAP (and optionally the STA client for a baseline run)
  DUT              : STA client -> scan / associate / ping / TCP TX / TCP RX / UDP TX / UDP RX

Verdicts come from two independent sources:
  1. RSSI/scan comparison against the reference board (same environment, same moment)
  2. absolute per-chip throughput thresholds (config/thresholds.json)
     plus an optional reference baseline measured in the same role

Usage:
  python wifi_hw_test.py --config config/astra_s3_vs_c6.json --out results/
  python wifi_hw_test.py --config ... --erase-nvs       # if association hangs (stale NVS)
  python wifi_hw_test.py --list-ports
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    print("pyserial is required:  pip install pyserial", file=sys.stderr)
    raise

# Board consoles emit bytes that a cp1252 Windows console cannot encode; never let
# printing kill a run (this bit us once already).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- console I/O


class Console:
    """A board's UART console. DTR/RTS are RELEASED on open.

    On boards where RTS->EN and DTR->BOOT0 are wired directly, asserting DTR/RTS
    holds the chip in reset or download mode and the console stays silent.
    """

    def __init__(self, port: str, baud: int = 115200, echo: bool = True):
        self.port = port
        self.echo = echo
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.ser.dtr = False
        self.ser.rts = False
        self.history: list[str] = []
        self._last, self._reps = None, 0
        self.panicked = False

    # -- low level
    def _emit(self, line: str) -> None:
        self.history.append(line)
        if re.search(r"Guru Meditation|panic'ed", line):
            self.panicked = True
        if not self.echo:
            return
        if line == self._last:
            self._reps += 1
            if self._reps > 3:
                return
        else:
            if self._reps > 3:
                print("      ... (%d identical lines suppressed)" % (self._reps - 3), flush=True)
            self._last, self._reps = line, 1
        print("[%s] %s" % (self.port, line), flush=True)

    def drain(self, secs: float, sink: list[str] | None = None) -> list[str]:
        out, end = [], time.time() + secs
        while time.time() < end:
            raw = self.ser.readline().decode("utf-8", "replace").rstrip()
            if not raw:
                continue
            self._emit(raw)
            out.append(raw)
            if sink is not None:
                sink.append(raw)
        return out

    def send(self, cmd: str, wait: float = 2.0, sink: list[str] | None = None) -> list[str]:
        print(">>> [%s] %s" % (self.port, cmd), flush=True)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.ser.write((cmd + "\r\n").encode())
        self.ser.flush()
        return self.drain(wait, sink)

    def wait_for(self, pattern: str, timeout: float) -> tuple[bool, list[str]]:
        rx, out, end = re.compile(pattern), [], time.time() + timeout
        while time.time() < end:
            raw = self.ser.readline().decode("utf-8", "replace").rstrip()
            if not raw:
                continue
            self._emit(raw)
            out.append(raw)
            if rx.search(raw):
                return True, out
        return False, out

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


# ------------------------------------------------------------------- parsers

SCAN_AP = re.compile(r"\+SCAN:\[(?P<bssid>[0-9a-fA-F:]{17})\]\[(?P<ssid>[^\]]*)\]\[rssi=(?P<rssi>-?\d+)\]")
SCAN_DONE = re.compile(r"SCAN_DONE:\s*Found\s+(\d+)\s+APs")
MBITS = re.compile(r"^\s*(?P<a>[\d.]+)\s*-\s*(?P<b>[\d.]+)\s*sec\s+(?P<mb>[\d.]+)\s+Mbits/sec")
STA_IP = re.compile(r"sta ip:\s*(\d+\.\d+\.\d+\.\d+)")
RSSI = re.compile(r"rssi[=:]\s*(-?\d+)")
PING_REPLY = re.compile(r"icmp_seq=(\d+).*?time=([\d.]+)\s*ms")
PING_STATS = re.compile(r"(\d+)\s+packets transmitted,\s*(\d+)\s+received")


def parse_scan(lines: list[str]) -> dict:
    """-> {'ap_count': int|None, 'aps': {bssid: {'ssid':..,'rssi':..}}}"""
    aps: dict[str, dict] = {}
    count = None
    for ln in lines:
        m = SCAN_AP.search(ln)
        if m:
            aps[m.group("bssid").lower()] = {"ssid": m.group("ssid"), "rssi": int(m.group("rssi"))}
        m = SCAN_DONE.search(ln)
        if m:
            count = int(m.group(1))
    return {"ap_count": count if count is not None else len(aps), "aps": aps}


def parse_throughput(lines: list[str], interval: float) -> dict:
    """Average the per-interval samples; drop final summaries and stale-server reports."""
    samples = []
    for ln in lines:
        m = MBITS.match(ln)
        if not m:
            continue
        span = float(m.group("b")) - float(m.group("a"))
        if span > interval * 1.5:          # final summary or leftover server report
            continue
        samples.append(float(m.group("mb")))
    good = [s for s in samples if s > 0]
    if not good:
        return {"avg": None, "min": None, "max": None, "n": 0, "samples": samples}
    return {"avg": statistics.mean(good), "min": min(good), "max": max(good),
            "n": len(good), "samples": good}


def parse_ping(lines: list[str]) -> dict:
    times, sent, recv = [], None, None
    for ln in lines:
        m = PING_REPLY.search(ln)
        if m:
            times.append(float(m.group(2)))
        m = PING_STATS.search(ln)
        if m:
            sent, recv = int(m.group(1)), int(m.group(2))
    recv = recv if recv is not None else len(times)
    sent = sent if sent is not None else recv
    return {"sent": sent, "recv": recv, "loss_pct": (100.0 * (sent - recv) / sent) if sent else None,
            "avg_ms": statistics.mean(times) if times else None,
            "max_ms": max(times) if times else None, "n": len(times)}


# ------------------------------------------------------------------- runner


class WifiTest:
    def __init__(self, cfg: dict, thresholds: dict, official: dict | None = None, echo: bool = True):
        self.cfg = cfg
        self.th = thresholds
        self.official = official or {}
        self.echo = echo
        self.dut = Console(cfg["dut"]["port"], echo=echo)
        self.ref = Console(cfg["reference"]["port"], echo=echo)
        self.ap = cfg["ap"]
        self.dur = cfg.get("durations", {})
        self.runs = cfg.get("tests", {})
        self.result: dict = {
            "started": datetime.now().isoformat(timespec="seconds"),
            "config": cfg, "dut": {}, "reference": {}, "tests": {}, "notes": [],
        }
        self.dut_ip = None
        self.ref_ip = None
        self.dut_rssi = None
        self.ref_rssi = None

    # -- helpers
    def log(self, msg: str) -> None:
        print("\n===== %s =====" % msg, flush=True)
        self.result["notes"].append(msg)

    def official_for(self, board_key: str) -> dict:
        """Espressif's published reference figures for this chip (never used as a pass/fail limit)."""
        chip = (self.cfg[board_key].get("chip") or "").lower()
        return (self.official.get("chips") or {}).get(chip, {})

    def official_sources(self) -> dict:
        return {"note": self.official.get("_conditions", ""), "source": self.official.get("_source", "")}

    def chip_thresholds(self, board_key: str) -> dict:
        chip = (self.cfg[board_key].get("chip") or "default").lower()
        merged = {**self.th.get("default", {}), **self.th.get(chip, {})}
        if board_key == "dut":                     # per-run override, e.g. mixed-antenna reference
            merged.update(self.cfg.get("thresholds", {}))
        return merged

    def stop_iperf(self, *keys: str) -> None:
        """The IDF iperf example keeps a server alive and refuses a second instance
        ('iperf is already running'), so abort any running one before the next test."""
        for k in keys:
            c = self.dut if k == "dut" else self.ref
            c.send("iperf --abort", 1.5)

    def identify(self, board_key: str) -> dict:
        """Board identity from the esptool binary if configured, else from the console.

        esptool needs exclusive access to the port, so the console is closed and reopened
        around the probe (the reopen also resets the board into a known state).
        """
        c = self.dut if board_key == "dut" else self.ref
        info = {"port": c.port, "name": self.cfg[board_key].get("name", board_key),
                "chip": self.cfg[board_key].get("chip", "unknown")}
        tool = self.cfg.get("esptool") or {}
        py, script = tool.get("python"), tool.get("path")
        if py and script and os.path.exists(script):
            c.close()
            time.sleep(0.4)
            try:
                chip = self.cfg[board_key].get("chip") or "auto"
                p = subprocess.run([py, script, "--chip", chip, "-p", c.port, "chip_id"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=90)
                txt = (p.stdout or "") + (p.stderr or "")
                m = re.search(r"Chip is ([^\n(]+)", txt)
                if m:
                    info["chip_detected"] = m.group(1).strip()
                m = re.search(r"MAC:\s*([0-9a-fA-F:]{17})", txt)
                if m:
                    info["mac"] = m.group(1)
                m = re.search(r"Features:\s*([^\n]+)", txt)
                if m:
                    info["features"] = m.group(1).strip()
                if not info.get("chip_detected"):
                    info["probe_note"] = (txt.strip().splitlines() or ["no output"])[-1]
            except Exception as exc:      # console data alone is enough to continue
                info["probe_error"] = str(exc)
            finally:
                fresh = Console(c.port, echo=self.echo)
                if board_key == "dut":
                    self.dut = fresh
                else:
                    self.ref = fresh
                boot = fresh.drain(3)
                # provenance: which IDF / wifi firmware / PHY the numbers belong to
                bt = "\n".join(boot)
                for key, pat in (("idf_version", r"ESP-IDF v?([0-9][0-9.]*)"),
                                 ("wifi_fw_version", r"wifi firmware version:\s*(\S+)"),
                                 ("phy_version", r"phy_version\s+([0-9a-fA-F.,]+)")):
                    m = re.search(pat, bt)
                    if m:
                        info[key] = m.group(1)
        return info

    def scan(self, board_key: str) -> dict:
        c = self.dut if board_key == "dut" else self.ref
        cmd = self.cfg.get("scan_command", "scan")
        self.log("SCAN on %s (%s)" % (board_key, c.port))
        c.send("wifi_mode sta", 1.5)
        lines = c.send(cmd, self.dur.get("scan", 15))
        return parse_scan(lines)

    def setup_softap(self, board_key: str) -> None:
        c = self.dut if board_key == "dut" else self.ref
        self.log("SoftAP on %s (%s)" % (board_key, c.port))
        c.send("wifi_mode ap", 2)
        c.send("ap_set %s %s%s" % (self.ap["ssid"], self.ap["password"],
                                   "" if self.ap.get("pmf", "off") == "off" else " --pmf_required"), 3)
        c.drain(4)

    def associate(self, board_key: str, attempts: int = 4) -> tuple[str | None, int | None]:
        c = self.dut if board_key == "dut" else self.ref
        self.log("ASSOCIATE %s -> %s" % (board_key, self.ap["ssid"]))
        for i in range(1, attempts + 1):
            print("--- attempt %d ---" % i, flush=True)
            c.send("sta_connect %s %s" % (self.ap["ssid"], self.ap["password"]), 1)
            ok, lines = c.wait_for(r"sta ip:|Guru Meditation", 22)
            if c.panicked:
                self.result["tests"].setdefault("panics", []).append(
                    {"board": board_key, "phase": "associate", "attempt": i})
                print("!! %s panicked; waiting for reboot" % board_key, flush=True)
                c.panicked = False
                c.drain(8)
                continue
            ip, rssi = None, None
            for ln in lines:
                m = STA_IP.search(ln)
                if m:
                    ip = m.group(1)
                m = RSSI.search(ln)
                if m:
                    rssi = int(m.group(1))
            if ip:
                # the driver prints the link RSSI in a line that can arrive right after the
                # IP event, so look again at everything seen since the attempt started
                extra = c.drain(2)
                for ln in lines + extra + c.history[-40:]:
                    m = RSSI.search(ln)
                    if m:
                        rssi = int(m.group(1))
                return ip, rssi
        return None, None

    def run(self) -> dict:
        dut_t, ref_t = self.chip_thresholds("dut"), self.chip_thresholds("reference")
        d, r = self.cfg["dut"], self.cfg["reference"]
        print("DUT   : %s @ %s" % (d.get("name", "dut"), d["port"]), flush=True)
        print("REF   : %s @ %s" % (r.get("name", "ref"), r["port"]), flush=True)

        self.dut.drain(2)
        self.ref.drain(2)
        self.result["dut"]["identity"] = self.identify("dut")
        self.result["reference"]["identity"] = self.identify("reference")
        self.result["dut"]["official"] = self.official_for("dut")
        self.result["reference"]["official"] = self.official_for("reference")
        self.result["official_meta"] = self.official_sources()

        # 1. scans (both boards, same moment/environment)
        th = self.cfg.get("esptool")
        dut_scan = self.scan("dut") if self.runs.get("scan", True) else {"ap_count": None, "aps": {}}
        ref_scan = self.scan("reference") if self.runs.get("scan", True) else {"ap_count": None, "aps": {}}
        self.result["dut"]["scan"] = dut_scan
        self.result["reference"]["scan"] = ref_scan

        # 2. reference becomes the SoftAP
        self.setup_softap("reference")

        # 3. DUT associates
        if self.runs.get("assoc", True):
            self.dut_ip, self.dut_rssi = self.associate("dut")
            self.result["dut"]["ip"] = self.dut_ip
            self.result["dut"]["rssi_dbm"] = self.dut_rssi
            if not self.dut_ip:
                self.result["notes"].append("DUT failed to associate - remaining data tests skipped")

        iv, tcp, udp = self.dur.get("interval", 2), self.dur.get("tcp", 15), self.dur.get("udp", 10)
        # -b is the OFFERED load (Mbit/s), not a measurement limit: iperf never reports more than it
        # was asked to send, so the offer must sit at or above the expected capacity or the result is
        # capped by the offer instead of by the link.
        bw = self.dur.get("udp_bitrate", 40)
        T = self.result["tests"]

        if self.dut_ip:
            if self.runs.get("ping", True):
                self.log("PING %s -> %s" % (self.dut_ip, self.ap["ip"]))
                T["ping"] = parse_ping(self.dut.send("ping %s" % self.ap["ip"], self.dur.get("ping", 12)))

            if self.runs.get("tcp_tx", True):
                self.log("TCP TX: DUT -> reference")
                T["tcp_tx"] = self._iperf_dut_tx(tcp, iv)

            if self.runs.get("tcp_rx", True):
                self.log("TCP RX: reference -> DUT")
                T["tcp_rx"] = self._iperf_dut_rx(tcp, iv)

            if self.runs.get("udp_tx", True):
                self.log("UDP TX: DUT -> reference")
                T["udp_tx"] = self._iperf_dut_tx(udp, iv, udp=True, bitrate=bw)

            if self.runs.get("udp_rx", True):
                self.log("UDP RX: reference -> DUT")
                T["udp_rx"] = self._iperf_dut_rx(udp, iv, udp=True, bitrate=bw)

            # optional UDP offered-load sweep: shows where the link saturates / starts dropping.
            # Reference information only - it does not change any verdict.
            rates = self.cfg.get("udp_bitrates") or []
            if rates:
                sweep = {}
                for rate in rates:
                    self.log("UDP TX sweep at %s Mbit/s offered" % rate)
                    sweep[str(rate)] = self._iperf_dut_tx(udp, iv, udp=True, bitrate=int(rate))
                T["udp_tx_sweep"] = sweep

        # 4. optional baseline: reference board in the client role, same conditions
        if self.cfg.get("reference_baseline", False):
            base = {}
            try:
                self.log("BASELINE: reference as STA client (roles swapped)")
                self.setup_softap("dut")
                self.ref_ip, self.ref_rssi = self.associate("reference")
                if self.ref_ip:
                    self.log("BASELINE TCP TX: reference -> DUT")
                    base["tcp_tx"] = self._iperf_swap_tx(tcp, iv)
                    self.log("BASELINE TCP RX: DUT -> reference")
                    base["tcp_rx"] = self._iperf_swap_rx(tcp, iv)
            except Exception as exc:
                base["error"] = str(exc)
            self.result["reference"]["baseline"] = base

        self.result["finished"] = datetime.now().isoformat(timespec="seconds")
        self.result["dut"]["thresholds"] = dut_t
        self.result["reference"]["thresholds"] = ref_t
        return self.result

    # -- iperf helpers (DUT as client, then DUT as server)
    def _iperf_dut_tx(self, seconds, iv, udp=False, bitrate=40) -> dict:
        flag = " -u -b %d" % bitrate if udp else ""
        self.stop_iperf("dut", "reference")
        self.ref.send("iperf -s%s -i %d" % (" -u" if udp else "", iv), 2)
        lines = self.dut.send("iperf -c %s%s -i %d -t %d" % (self.ap["ip"], flag, iv, seconds), seconds + 6)
        return parse_throughput(lines, iv)

    def _iperf_dut_rx(self, seconds, iv, udp=False, bitrate=40) -> dict:
        flag = " -u -b %d" % bitrate if udp else ""
        self.stop_iperf("dut", "reference")
        self.dut.send("iperf -s%s -i %d" % (" -u" if udp else "", iv), 2)
        lines = self.ref.send("iperf -c %s%s -i %d -t %d" % (self.dut_ip, flag, iv, seconds), seconds + 6)
        return parse_throughput(lines, iv)

    def _iperf_swap_tx(self, seconds, iv) -> dict:
        self.stop_iperf("dut", "reference")
        self.dut.send("iperf -s -i %d" % iv, 2)
        lines = self.ref.send("iperf -c %s -i %d -t %d" % (self.ap["ip"], iv, seconds), seconds + 6)
        return parse_throughput(lines, iv)

    def _iperf_swap_rx(self, seconds, iv) -> dict:
        self.stop_iperf("dut", "reference")
        self.ref.send("iperf -s -i %d" % iv, 2)
        lines = self.dut.send("iperf -c %s -i %d -t %d" % (self.ref_ip, iv, seconds), seconds + 6)
        return parse_throughput(lines, iv)

    def close(self) -> None:
        self.dut.close()
        self.ref.close()


# ------------------------------------------------------------------ verdicts


def verdicts(res: dict, thresholds: dict) -> list[dict]:
    dut, ref, tests = res["dut"], res["reference"], res["tests"]
    th_dut = dut.get("thresholds", thresholds.get("default", {}))
    th_ref = ref.get("thresholds", thresholds.get("default", {}))
    cfg = res["config"]
    same_chip = (cfg["dut"].get("chip", "").lower() == cfg["reference"].get("chip", "").lower())
    V = []

    def add(name, ok, detail):
        V.append({"test": name, "result": "PASS" if ok is True else ("FAIL" if ok is False else "SKIP"),
                  "detail": detail, "ok": ok})

    # scan comparison
    d, r = dut.get("scan", {}), ref.get("scan", {})
    if d.get("ap_count") is not None and r.get("ap_count"):
        ratio = d["ap_count"] / r["ap_count"]
        common = set(d["aps"]) & set(r["aps"])
        deltas = [d["aps"][b]["rssi"] - r["aps"][b]["rssi"] for b in common]
        med = statistics.median(deltas) if deltas else None
        ok_ratio = ratio >= th_dut.get("scan_ap_ratio_min", 0.6)
        ok_rssi = (med is None) or (abs(med) <= th_dut.get("rssi_delta_db_max", 8))
        add("scan (RX sensitivity vs reference)", ok_ratio and ok_rssi,
            "%d APs vs %d reference (%.0f%%), %d common BSSIDs, median RSSI delta %s dB" %
            (d["ap_count"], r["ap_count"], ratio * 100, len(common),
             ("%.1f" % med) if med is not None else "n/a"))
    else:
        add("scan (RX sensitivity vs reference)", None, "scan data unavailable")

    # association
    if cfg.get("tests", {}).get("assoc", True):
        rssi = dut.get("rssi_dbm")
        min_rssi = th_dut.get("min_rssi_dbm", -70)
        if dut.get("ip"):
            add("association", (rssi is None) or (rssi >= min_rssi),
                "ip=%s rssi=%s dBm (min %d)" % (dut["ip"], rssi, min_rssi))
        else:
            add("association", False, "no IP obtained")

    # ping
    p = tests.get("ping")
    if p:
        loss = p.get("loss_pct") or 0
        add("ping", loss <= th_dut.get("ping_loss_pct_max", 5),
            "%d/%d replies, loss %.0f%%, avg %s ms" % (p["recv"], p["sent"], loss,
                                                       ("%.0f" % p["avg_ms"]) if p["avg_ms"] else "n/a"))

    # throughput per test
    base = res["reference"].get("baseline", {})
    for key, label, thkey, basekey in (
            ("tcp_tx", "TCP TX (DUT -> reference)", "tcp_min_mbit", "tcp_tx"),
            ("tcp_rx", "TCP RX (reference -> DUT)", "tcp_min_mbit", "tcp_rx"),
            ("udp_tx", "UDP TX (DUT -> reference)", "udp_min_mbit", None),
            ("udp_rx", "UDP RX (reference -> DUT)", "udp_min_mbit", None)):
        t = tests.get(key)
        if not t or t.get("avg") is None:
            if cfg.get("tests", {}).get(key, True):
                add(label, False, "no measurement (association or iperf failed)")
            continue
        avg = t["avg"]
        floor = th_dut.get(thkey, 0)
        ok = avg >= floor
        detail = "%.2f Mbit/s avg (min %.2f, max %.2f) floor %.0f" % (avg, t["min"], t["max"], floor)
        if key.startswith("udp"):
            offer = cfg.get("durations", {}).get("udp_bitrate")
            if offer:
                detail += "; offered -b %d Mbit/s" % offer
                if avg >= 0.9 * offer:
                    detail += " - achieved tracks the offer, so the offer capped the result (raise -b)"
                else:
                    detail += " - link ceiling below the offer, so this is a real measurement"
        b = base.get(basekey) if basekey else None
        if b and b.get("avg"):
            ratio = avg / b["avg"]
            need = th_dut.get("ref_ratio_min", 0.7) if same_chip else 0.5
            ok = ok and ratio >= need
            detail += "; reference %.2f Mbit/s, ratio %.2f (need %.2f%s)" % (
                b["avg"], ratio, need, "" if same_chip else ", mixed chips -> indicative")
        add(label, ok, detail)

    for pn in tests.get("panics", []):
        add("no panic on %s" % pn["board"], False, "panic during %s (attempt %d)" % (pn["phase"], pn["attempt"]))

    return V


def build_report(res: dict, V: list[dict]) -> str:
    cfg, dut, ref, tests = res["config"], res["dut"], res["reference"], res["tests"]
    overall = all(v["ok"] for v in V) if V else False
    L = []
    L.append("# WiFi hardware test report — %s" % cfg.get("name", "unnamed"))
    L.append("")
    L.append("- **Started:** %s" % res.get("started"))
    L.append("- **Finished:** %s" % res.get("finished"))
    L.append("- **Result: %s**" % ("✅ PASS" if overall else "❌ FAIL"))
    for note in cfg.get("run_notes", []):
        L.append("- ⚠️ %s" % note)
    L.append("")
    L.append("## Boards")
    L.append("")
    L.append("| Role | Name | Port | Chip | MAC | Features |")
    L.append("|---|---|---|---|---|---|")
    for key, label in (("dut", "DUT (device under test)"), ("reference", "Reference (known good)")):
        i = res[key]["identity"]
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            label, i.get("name"), i.get("port"), i.get("chip_detected") or i.get("chip"),
            i.get("mac", "?"), (i.get("features", "") or "")[:48]))
    L.append("")
    L.append("## Provenance (which firmware produced these numbers)")
    L.append("")
    L.append("| Role | ESP-IDF | WiFi firmware | PHY version |")
    L.append("|---|---|---|---|")
    for key, label in (("dut", "DUT"), ("reference", "Reference")):
        i = res[key]["identity"]
        L.append("| %s | %s | %s | %s |" % (label, i.get("idf_version", "?"),
                                           i.get("wifi_fw_version", "?"), i.get("phy_version", "?")))
    om = res.get("official_meta", {})
    dut_off = dut.get("official", {}) or {}
    if dut_off.get("idf_commit"):
        L.append("")
        L.append("> Official reference figures below were measured by Espressif on IDF commit "
                 "`%s` (%s) - **not** on the IDF version used for this run." %
                 (dut_off.get("idf_commit"), dut_off.get("idf_commit_date", "?")))
    L.append("")
    L.append("## Verdicts")
    L.append("")
    L.append("| Test | Result | Detail |")
    L.append("|---|---|---|")
    for v in V:
        icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "➖ SKIP"}[v["result"]]
        L.append("| %s | %s | %s |" % (v["test"], icon, v["detail"]))
    L.append("")
    L.append("## Scan comparison (same environment, same moment)")
    L.append("")
    ds, rs = dut.get("scan", {}), ref.get("scan", {})
    if ds.get("aps") is not None and rs.get("aps") is not None:
        L.append("- DUT: **%s APs**; reference: **%s APs**" % (ds.get("ap_count"), rs.get("ap_count")))
        common = sorted(set(ds.get("aps", {})) & set(rs.get("aps", {})))
        if common:
            L.append("")
            L.append("| SSID | BSSID | DUT RSSI | ref RSSI | delta |")
            L.append("|---|---|---|---|---|")
            for b in common[:15]:
                a = ds["aps"][b]; c = rs["aps"][b]
                L.append("| %s | %s | %d | %d | %+d |" % (a["ssid"], b, a["rssi"], c["rssi"],
                                                           a["rssi"] - c["rssi"]))
    L.append("")
    L.append("## Link")
    L.append("")
    L.append("- DUT associated: `ip=%s`, RSSI `%s dBm`" % (dut.get("ip"), dut.get("rssi_dbm")))
    p = tests.get("ping")
    if p:
        L.append("- Ping: %d/%d replies, avg %s ms, max %s ms" % (
            p["recv"], p["sent"],
            ("%.0f" % p["avg_ms"]) if p["avg_ms"] else "n/a",
            ("%.0f" % p["max_ms"]) if p["max_ms"] else "n/a"))
    L.append("")
    L.append("## Throughput")
    L.append("")
    off_air, off_shield = dut_off.get("air", {}), dut_off.get("shield", {})
    L.append("| Test | DUT avg | min | max | reference baseline | ratio | official air | official shield |")
    L.append("|---|---|---|---|---|---|---|---|")
    base = ref.get("baseline", {})
    udp_offer = cfg.get("durations", {}).get("udp_bitrate")
    for key, label, bk in (("tcp_tx", "TCP TX (DUT → ref)", "tcp_tx"),
                           ("tcp_rx", "TCP RX (ref → DUT)", "tcp_rx"),
                           ("udp_tx", "UDP TX (DUT → ref)", None),
                           ("udp_rx", "UDP RX (ref → DUT)", None)):
        t = tests.get(key) or {}
        if key.startswith("udp") and udp_offer:
            label += " @ `-b %d` offered" % udp_offer
        b = base.get(bk) if bk else None
        ratio = ("%.2f" % (t["avg"] / b["avg"])) if (t.get("avg") and b and b.get("avg")) else "—"
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            label,
            ("%.2f Mbit/s" % t["avg"]) if t.get("avg") else "—",
            ("%.2f" % t["min"]) if t.get("min") else "—",
            ("%.2f" % t["max"]) if t.get("max") else "—",
            ("%.2f Mbit/s" % b["avg"]) if b and b.get("avg") else "not measured",
            ratio,
            ("%s Mbit/s" % off_air.get(key)) if off_air.get(key) else "—",
            ("%s Mbit/s" % off_shield.get(key)) if off_shield.get(key) else "—"))
    L.append("")
    L.append("`official air` / `official shield` are Espressif's published best-case figures for %s "
             "(%s) - **reference only, never used as a pass/fail limit**; the shield-box column needs "
             "a shielded box and a router peer. %s" %
             (dut_off.get("name", dut.get("identity", {}).get("chip", "this chip")),
              ("IDF commit %s, %s" % (dut_off.get("idf_commit"), dut_off.get("idf_commit_date")))
              if dut_off.get("idf_commit") else "no published table for this chip",
              om.get("source", "")))
    sweep = tests.get("udp_tx_sweep")
    if tests.get("udp_tx") or tests.get("udp_rx"):
        L.append("")
        L.append("`-b` is the **offered** UDP load, in Mbit/s: iperf never reports more than it was asked "
                 "to send, so the UDP rows are only meaningful together with the offer shown. An offer "
                 "below the link's real capacity caps the result; run at `-b 40` (above the 30 Mbit/s "
                 "documented air figure) to measure the link instead of the offer.")
    if sweep:
        L.append("")
        L.append("### UDP offered-load sweep (DUT → ref, reference only)")
        L.append("")
        L.append("| Offered `-b` | Achieved avg | min | max | reading |")
        L.append("|---|---|---|---|---|")
        for rate, s in sweep.items():
            achieved = s.get("avg")
            if achieved:
                if achieved >= 0.9 * float(rate):
                    reading = "capped by the offer"
                else:
                    reading = "link ceiling"
            else:
                reading = "no data"
            L.append("| %s Mbit/s | %s | %s | %s | %s |" % (
                rate,
                ("%.2f Mbit/s" % achieved) if achieved else "no data",
                ("%.2f" % s["min"]) if s.get("min") else "—",
                ("%.2f" % s["max"]) if s.get("max") else "—",
                reading))
        L.append("")
        L.append("`-b` is the **offered load** in Mbit/s — iperf never reports more than it was asked to "
                 "send, so `achieved ≈ offered` means the offer, not the link, set the number. Raise `-b` "
                 "until the achieved value stops rising; that plateau is the link ceiling.")
    L.append("")
    if res.get("notes"):
        L.append("## Run log")
        L.append("")
        for n in res["notes"]:
            L.append("- %s" % n)
        L.append("")
    L.append("## How to reproduce")
    L.append("")
    L.append("```")
    L.append("python wifi_hw_test.py --config <this config> --out results/")
    L.append("```")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------- main


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser(description="ESP32 WiFi hardware verification harness")
    ap.add_argument("--config", help="JSON config (see config/example_*.json)")
    ap.add_argument("--thresholds", default=None, help="override thresholds json")
    ap.add_argument("--out", default="results", help="output directory for the report")
    ap.add_argument("--erase-nvs", action="store_true",
                    help="erase the DUT NVS partition before testing (fixes stale-config hangs)")
    ap.add_argument("--name", default=None, help="report name override")
    ap.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    ap.add_argument("--quiet", action="store_true", help="do not echo raw console lines")
    args = ap.parse_args()

    if args.list_ports:
        for p in list_ports.comports():
            print("%-8s %s" % (p.device, p.description))
        return 0
    if not args.config:
        ap.error("--config is required (or use --list-ports)")

    cfg = load_json(args.config)
    here = os.path.dirname(os.path.abspath(__file__))
    th_path = args.thresholds or cfg.get("thresholds_file") or os.path.join(here, "config", "thresholds.json")
    if not os.path.isabs(th_path):
        th_path = os.path.join(here, th_path)
    thresholds = load_json(th_path)
    off_path = cfg.get("official_file") or os.path.join(here, "config", "official_throughput.json")
    if not os.path.isabs(off_path):
        off_path = os.path.join(here, off_path)
    official = load_json(off_path) if os.path.exists(off_path) else {}
    cfg.setdefault("esptool", {})
    if cfg["esptool"].get("path") and not os.path.isabs(cfg["esptool"]["path"]):
        cfg["esptool"]["path"] = os.path.join(here, cfg["esptool"]["path"])

    if args.name:
        cfg["name"] = args.name

    if args.erase_nvs:
        tool = cfg.get("esptool", {})
        cmd = [tool.get("python", sys.executable), tool.get("path", "esptool.py"),
               "--chip", cfg["dut"].get("chip", "auto"), "-p", cfg["dut"]["port"],
               "--before", "default_reset", "--after", "hard_reset",
               "erase_region", cfg.get("nvs_offset", "0x9000"), cfg.get("nvs_size", "0x6000")]
        print(">>> erasing NVS:", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=False)
        time.sleep(3)

    t = WifiTest(cfg, thresholds, official=official, echo=not args.quiet)
    try:
        res = t.run()
    finally:
        t.close()

    V = verdicts(res, thresholds)
    report = build_report(res, V)
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cfg.get("name", "run"))
    path = os.path.join(args.out, "%s_%s.md" % (stamp, safe))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)
    print("\n" + report, flush=True)
    print("\nReport written: %s" % path, flush=True)
    return 0 if all(v["ok"] for v in V) else 1


if __name__ == "__main__":
    sys.exit(main())
