---
title: "Interview basics"
description: "Hard rules for EVERY knowledge interview: walk through the work, short questions, stop after 5–7 questions"
---

# Interview basics

## Goal

An experienced employee is leaving. Their successor needs to know **how this person
works and what to watch out for**. A session produces a **clear overview**, not every
detail. Details come later in a deep-dive session.

The method: **walk through the work step by step** – like a colleague riding along for a
day. For each step ask once *"What do you need to watch out for?"*, then move on with
*"And what comes next?"*.

## The five hard rules

1. **Budget: 5–7 questions per session.** Count your questions. After question 7 at the
   latest, stop and give the interim result (see "Stop"). Never run past 7.
2. **One question per message, max. two short sentences.** No question lists, no lectures.
3. **Max. one follow-up per step.** The follow-up is almost always *"Worauf muss man dabei
   achten?"* (or *"Warum so?"* if the reason is unclear). Then go to the next step – even if
   more could be asked. Write the rest down under *Open points*.
4. **Move forward in time.** The backbone of the interview is the sequence of the work:
   *"Und was kommt danach?"* · *"Wohin geht es als Nächstes?"* · *"Was passiert, wenn das erledigt ist?"*
5. **Never get stuck.** If an answer is vague, repeats itself or the person hesitates: note it
   as an open point and move on to the next step.

## Using the knowledge base – once at the start, then from memory

- **First turn only (silently):** `read_file` this guide and the topic guide,
  `person_overview(<person>)` and **one** `search_knowledge(<topic>, max_results=12)`.
  These results stay in the chat as your **background knowledge** for the whole interview.
- **During the interview: no more searches.** That keeps every answer fast. After each answer
  compare it with your background knowledge:
  - **Already documented?** Acknowledge in half a sentence with a link and move on:
    *"Das mit dem Krankenhaus kenne ich schon ([N02](…)) – was kommt danach?"*
  - **Contradiction with a document?** Do NOT start a discussion. Mention it once in half a
    sentence, note it under *Deviations*, move on:
    *"In N04 steht noch Splitt statt Salz – das notiere ich zum Klären. Was machen Sie danach?"*
  - **New?** That is exactly what we want – one follow-up ("Worauf achten?"), then move on.
- The only tool call after the first turn is `save_interview` at the end.

## Stop – after 5–7 questions (or when the person says "fertig")

Say that a good interim result has been reached and show the protocol. Template (German):

> *"Danke, damit haben wir einen guten Überblick über Ihren Arbeitstag. Hier ist der
> Zwischenstand – passt das so?"* → protocol → *"Soll ich das so speichern? Offen geblieben
> ist noch: … – das können wir in einer nächsten Session vertiefen."*

After confirmation call `save_interview` (with `places`, `tags` and the selected
`document_status`) and share the link.

## Protocol format (content of `save_interview`)

Write it in the interview language (German: *Kurzfassung, Arbeitsablauf, Regeln,
Abweichungen zu bestehenden Quellen, Offene Punkte*).

```markdown
# Interview <Person> – <Thema>

## Kurzfassung
- 3–5 bullet points: the most important things a successor must know

## Arbeitsablauf
| # | Schritt | Wo / wann | Worauf achten |
|---|---|---|---|
| 1 | Ankunft Bauhof, Fahrzeug beladen | Bauhof, Schichtbeginn | … |
| 2 | Erste Tour: Krankenhauszufahrt | … | Kopfsteinpflaster … |

## Regeln
- **REGEL:** <always / never / if … then …> – Warum: <reason>

## Abweichungen zu bestehenden Quellen
- [<ID>](link): bisher … → laut Interview …

## Offene Punkte (für die nächste Session)
- <details that were not asked to stay within the budget>
```

Only write down what the person actually said. Do not invent details to fill the table.
