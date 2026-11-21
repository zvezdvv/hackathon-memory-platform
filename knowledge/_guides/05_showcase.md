---
title: "Showcase – two questions"
description: "Short live demo: two questions about one critical place, then the protocol is saved straight away and appears on the map"
---

# Showcase interview: two questions

Use this guide **only** when the first message says `Mode: showcase`. It replaces the session
logic of the interview basics: no session number, no question budget of 5–7, no confirmation
step. Goal: show in under two minutes how experience becomes searchable knowledge – with a
source and a pin on the map.

## First turn (silently)

`read_file` this guide and `search_knowledge(<topic>)`. Nothing else.

## Exactly two questions

| # | Question (German) | What you want |
|---|---|---|
| 1 | *"Welche Stelle auf Ihrer Tour würde ein Neuer garantiert unterschätzen – und was passiert dort?"* | a concrete place (street, bridge, square) and the problem |
| 2 | *"Was muss man dort konkret tun – und ab wann?"* | the rule: action, time or threshold, who to call |

- Opening: one short sentence (*"Kurze Demo mit zwei Fragen zu <Thema>."*), then question 1.
- No follow-up questions. If an answer is vague, take it as it is.
- If the place is already documented, say so in half a sentence with the link in question 2
  (*"Zur Steingrube steht schon das Halteverbot ([N01](…)) – was muss man dort konkret tun?"*).

## After answer 2: save immediately

Call `save_interview` right away (no confirmation question) with:
- `session`: 0 (showcase, not part of the session sequence)
- `guide`: `_guides/05_showcase.md`
- `places`: every place the person named, as written (street names, bridges, squares)
- `tags`: 3–5 lowercase keywords
- `document_status`: the status from the first message

Then reply in two parts: the saved link, and the protocol in short form (Kurzfassung with
2–3 bullets and the **REGEL:**). End with *"Der Ort ist jetzt auf der Karte markiert."* when a
place was named.

## Protocol format (content of `save_interview`)

```markdown
# Interview <Person> – <Thema> (Showcase)

## Kurzfassung
- <Ort>: <Problem>
- <was zu tun ist, ab wann>

## Regeln
- **REGEL:** <Wenn … dann …> – Warum: <Grund>

## Offene Punkte (für die nächste Session)
- <was offen blieb, z. B. Zuständigkeit oder Schwellenwert>
```

Only write down what the person actually said.
