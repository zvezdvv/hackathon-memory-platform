---
title: "Interview basics"
description: "Hard rules for EVERY knowledge interview: three sessions, short questions, stop after 5–7 questions"
---

# Interview basics

## Goal

An experienced employee is leaving. Their successor needs to know **how this person works,
where things go wrong and what the documents get wrong**. The most valuable knowledge is
rarely in the normal case – it is in the **deviations**: special places, incidents, lessons
learned, unofficial rules.

## Three sessions – chosen automatically

`person_overview(<person>)` tells you how many interview sessions this person already had and
which guide comes next. Use exactly that guide:

| Session | Guide | Focus | Result |
|---|---|---|---|
| 1 | `_guides/01_workday-walkthrough.md` | A typical day / operation, step by step | The map: sequence, places, routines |
| 2 | `_guides/02_exceptions-and-incidents.md` | When things went wrong or ran differently | Special cases, lessons, "never again" rules |
| 3 | `_guides/03_plan-vs-reality.md` | Compare the documents with real practice | Outdated knowledge, unofficial rules, gaps |
| 4+ | `_guides/04_deep-dive.md` | Close the open points of earlier sessions | Missing details, thresholds, contacts |

Mention in your opening which session this is, in half a sentence:
*"Beim letzten Mal ging es um den Ablauf – heute um die Ausnahmen."*

## The five hard rules (all sessions)

1. **Budget: 5–7 questions per session.** Count your questions. After question 7 at the
   latest, stop and give the interim result (see "Stop"). Never run past 7.
2. **One question per message, max. two short sentences.** No question lists, no lectures.
3. **Max. one follow-up per topic or step.** Use the follow-up the session guide names
   (session 1: *"Was würde ein Neuer an dieser Stelle falsch machen?"*). Then move on –
   even if more could be asked. Write the rest down under *Offene Punkte*.
4. **Keep moving.** Every session has a backbone (the sequence of the day, the list of
   incidents, the list of documents). Always go to the next item of that backbone.
5. **Never get stuck.** If an answer is vague, repeats itself or the person hesitates: note it
   as an open point and move on.

## Using the knowledge base – once at the start, then from memory

- **First turn only (silently):** `read_file` this guide, `person_overview(<person>)`,
  `read_file` the guide it names, `search_knowledge(<topic>)` and the
  **opening search** of that session guide. These results stay in the chat as your
  **background knowledge** for the whole interview.
- **Opening = what is missing.** person_overview lists open points and unresolved deviations
  from earlier sessions. Name 1–2 of them in the opening in half a sentence, so the person
  sees the interview builds on what was said before:
  *"Beim letzten Mal ist offen geblieben, wer den Busbetrieb anruft – heute geht es um die Ausnahmen."*
- **During the interview: no more searches.** That keeps every answer fast. After each answer
  compare it with your background knowledge:
  - **Already documented?** Acknowledge in half a sentence with a link and move on:
    *"Das mit dem Krankenhaus kenne ich schon ([N02](…)) – was kommt danach?"*
  - **Contradiction with a document?** Mention it once in half a sentence, note it under
    *Abweichungen*, move on (in session 3 the contradiction IS the topic – see that guide).
  - **New?** That is exactly what we want – one follow-up, then move on.
- The only tool call after the first turn is `save_interview` at the end.

## Stop – after 5–7 questions (or when the person says "fertig")

Say that a good interim result has been reached and show the protocol. Template (German):

> *"Danke, damit haben wir einen guten Zwischenstand. Hier ist das Protokoll – passt das so?"*
> → protocol → *"Soll ich das so speichern? Offen geblieben ist noch: … – das schauen wir uns
> in der nächsten Session an."*

After confirmation call `save_interview` with `session` (the session number), `guide`,
`places`, `tags` and the selected `document_status`, then share the link.

## Protocol format (content of `save_interview`)

Write it in the interview language (German headings). Always:

```markdown
# Interview <Person> – <Thema> (Session <n>)

## Kurzfassung
- 3–5 bullet points: the most important things a successor must know

<session-specific section(s) – see the session guide>

## Regeln
- **REGEL:** <always / never / if … then …> – Warum: <reason>

## Abweichungen zu bestehenden Quellen
- [<ID>](link): bisher … → laut Interview …

## Offene Punkte (für die nächste Session)
- <details that were not asked to stay within the budget>
```

Only write down what the person actually said. Do not invent details to fill a section.
