---
title: "Capture a decision process"
description: "Turn 'how do you decide?' into ordered steps with thresholds, sources and branches"
---

# Guide: capture a decision process

Complements the interview basics. Goal: a process that someone new can follow at 3 a.m.
without calling the experienced person – with **ordered steps, decision thresholds, data
sources and alternative paths**.

Example of the target result (winter service, night decision):

| # | Step | Decision / threshold | Who | Tools / source |
|---|---|---|---|---|
| 1 | Check road temperature | below +1 °C → continue, otherwise re-check in 1 h | Night supervisor | Ice warning system Kennedydamm |
| 2 | Check snowfall / freezing rain forecast | snow ≥ 1 cm or freezing rain expected → continue | Night supervisor | DWD warnings |
| 3 | Look at the camera / do a check drive | ice or snow visible on bridges/slopes → dispatch | Night supervisor | Webcam, check drive from 03:30 |
| 4 | Dispatch crews | full deployment vs. only priority A | Night supervisor | Operations plan |
| 5 | Inform others | bus company before 05:00 if slopes not gritted; press on full deployment | Night supervisor | Contact list |

## Questions in this order

1. **Trigger** – "Was ist der Auslöser? Woran merken Sie, dass Sie aktiv werden müssen?"
2. **First look** – "Was schauen Sie sich als Allererstes an?" (data source, device, place)
3. **Step by step** – "Und dann? Was kommt danach?" – go through to the end before
   deepening. Keep the order exactly as the person does it.
4. **Thresholds** for every decision step – the most important part:
   - "Ab welchem Wert machen Sie weiter – und was machen Sie, wenn der Wert darunter liegt?"
   - "Wie sicher muss die Prognose sein?" · "Wie lange warten Sie ab?"
5. **Sources** – "Wo genau lesen Sie das ab?" (system, website, camera, phone call, check drive)
6. **People** – "Wer entscheidet, wer führt aus, wer muss informiert werden – bis wann?"
7. **Branches** – "Was machen Sie, wenn …"
   - the data source fails (camera offline, sensor wrong)?
   - the situation is unclear (forecast says no, but it looks icy)?
   - resources are short (crew sick, salt low, vehicle broken)?
8. **Time limits** – "Bis wann muss welcher Schritt erledigt sein?" (e.g. bus company before 05:00)
9. **Pitfalls** – "Welcher Fehler passiert Neuen hier am häufigsten?"
10. **Read back** – summarize the whole process as a numbered list and ask:
    *"Ist die Reihenfolge so richtig? Fehlt ein Schritt?"*

## Tips

- If the person says "it depends" – ask "Wovon genau?" until you get a measurable criterion.
- If the person says "you just see it" – ask for the last concrete case: "Was haben Sie
  da gesehen?"
- Cross-check with existing documents (operations plan, service instructions) using
  `search_knowledge` and mark deviations.

## What goes into the protocol

- Under **Processes**: the step table (step, decision/threshold, who, tools/source) plus
  the branches as "If … → …" lines.
- Under **Rules**: thresholds that apply on their own (e.g. "Below +1 °C at Kennedydamm →
  bridges first").
- In `save_interview`: places in `places`, keywords in `tags` (e.g. `einsatzentscheidung`,
  `glättemeldeanlage`, `kontrollfahrt`).
