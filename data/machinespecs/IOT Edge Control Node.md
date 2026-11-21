# Product Manual – Edge Control Node P4311 (Rev R3)

## 1. Overview
The P4311 is a compact variant featuring a single Ethernet uplink and reduced I/O count intended for lightweight control or telemetry edge roles. Shares common firmware base with P4324 (Series 4000 family) but omits redundant relay outputs.

## 2. Key Differences vs P4324
| Attribute | P4311 | P4324 |
|-----------|-------|-------|
| Ethernet Ports | 1 | 2 |
| Max Output Power via USB | 4.5 W | 7.5 W |
| Adapter (Nominal) | 24 V / 2.5 A (AD-60W-24) | 24 V / 3.75 A (AD-90W-24) |
| Thermal Headroom | Lower | Higher |

## 3. Indicators
| Indicator | States | Description |
|-----------|--------|-------------|
| PWR | Off / Green | DC rail presence |
| SYS | Blink (green) | Heartbeat (500 ms on / 1500 ms off) |
| ETH | Off / Green / Blink | Link status & activity |
| CTL11 | Green / Red | Controller link stability & session timing monitor |
| CTL2 | Green / Red | Secondary supervisory thread & update orchestration |

### 3.1 CTL11 Behavior
CTL11 monitors aggregated metrics:
- DHCP / static link negotiation success rate
- Session renewal jitter vs rolling baseline
- IPC heartbeat variance for supervisory service

| Condition | Expected CTL11 |
|-----------|----------------|
| Normal (variance < threshold) | Green |
| Transient link renegotiation event | Green (may brief blink) |
| Detected sustained inconsistency ( > 3 cycles ) | Red (latched until stability window passes) |
| Service restart (graceful) | Red (≤ 15 s) then Green |

Firmware 1.12 Observed Anomaly: On certain deployments with long but still within spec copper patch runs, CTL11 may falsely assert Red even though packet loss <0.1%. Patch under evaluation increases debounce window.

### 3.2 CTL2 Behavior
CTL2 covers a smaller scope: update staging and integrity verification. A Red CTL2 combined with normal SYS heartbeat typically signals an integrity hash mismatch or aborted write.

## 4. Power Adapter (AD-60W-24)
| Parameter | Value |
|-----------|-------|
| Output Voltage | 24.0 V DC |
| Max Current | 2.5 A |
| Max Power | 60 W |
| DC Plug | Barrel 5.5 mm OD / 2.1 mm ID center‑positive |

Replacement requirements: Must match voltage, polarity, and provide ≥ 2.5 A continuous at 24 V. For detachable AC cord, use characteristics meeting or exceeding: 18 AWG copper, temp rating 90 °C+, voltage rating ≥ region mains rating, flexible outer jacket.

## 5. Environmental
Same as P4324 except reduced thermal load; recommended placement with 10 mm side clearance.

## 6. Troubleshooting Summary
| Symptom | Action |
|---------|--------|
| CTL11 Red intermittently (10–20 s) | Collect diagnostics: `diag ctl11 snapshot`; Check firmware version (1.12 known). Do NOT relocate unit before ruling out firmware issue. |
| CTL2 Red persistent | Inspect update logs `log show update`; validate package signature. |
| PWR off; adapter LED on | Measure adapter output under load; ensure ≥ 22.5 V. |

## 7. Diagnostics Commands (Excerpt)
| Command | Purpose |
|---------|---------|
| diag ctl11 snapshot | Captures timing stability counters |
| diag net quality | Summarizes link error / retrans counts |
| diag bundle create | Packs logs for support escalation |

## 8. Firmware Notes
- 1.11: Improved session jitter calculation.
- 1.12: Introduced adaptive baseline — regression causing false CTL11 red under borderline latency spikes.
- Planned 1.13: Debounce fix + enhanced counters.

## 9. Document Change Log
| Revision | Date | Summary |
|----------|------|---------|
| R1 | 2024-07-12 | Initial release |
| R2 | 2024-11-30 | Added CTL2 integrity monitoring detail |
| R3 | 2025-02-10 | Clarified false CTL11 indication scenario |

---
Internal retrieval cues: part-number:P4311 indicator:CTL11 indicator:CTL2 firmware:1.12 intermittent:red-led guidance:do-not-move
