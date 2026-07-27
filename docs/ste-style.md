# Simplified Technical English style

This project uses ASD-STE100 Simplified Technical English for technical documentation.
The project uses Issue 9, dated January 15, 2025, as the reference standard.

ASD-STE100 has writing rules and a controlled dictionary.
The official dictionary is the authority for general words.
Project-specific names and actions can be technical nouns or technical verbs.

## Project rules

Use these rules for all Markdown files:

1. Use short sentences.
2. Use a maximum of 25 words in a descriptive sentence.
3. Use one instruction in each procedural sentence.
4. Use active voice when the actor is important.
5. Use present tense for system behavior.
6. Use the same word for the same meaning.
7. Do not use a synonym only to add variety.
8. Put conditions before instructions when this order helps the reader.
9. Use lists for sets of three or more items.
10. Use one topic in each paragraph.
11. Do not use semicolons in prose.
12. Do not use undefined abbreviations.
13. Keep code identifiers, command names, and protocol names unchanged.
14. Treat PostgreSQL names and project names as technical nouns.
15. Treat database operations as technical verbs when the context is clear.

## Approved project terms

The project uses technical nouns such as these terms:

- PostgreSQL
- Model Context Protocol
- MCP server
- SQL statement
- server-side cursor
- connection pool
- review hash
- migration ledger
- maintenance ledger
- replication slot
- WAL receiver
- extension profile
- provider profile

The project uses technical verbs such as these terms:

- commit
- roll back
- reindex
- vacuum
- analyze
- reconcile
- deserialize
- serialize

Use the spelling and meaning that this repository defines.
Do not change code identifiers to satisfy a language rule.

## Automated check

Run this command before you commit documentation changes:

```bash
python scripts/check_ste_docs.py .
```

The check is a mechanical aid.
It checks sentence length, selected prohibited phrases, heading punctuation, and semicolons.
It does not replace review by a writer who knows ASD-STE100.
