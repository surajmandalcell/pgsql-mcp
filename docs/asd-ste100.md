# ASD-STE100 documentation style

Repository Markdown uses Simplified Technical English principles from ASD-STE100 Issue 9.

This project does not reproduce the copyrighted standard.

Use the official standard as the authority.

## Project rules

- Use one approved meaning for each word.
- Use short sentences.
- Use no more than 25 words in a descriptive sentence.
- Use one instruction in each procedural sentence.
- Use active voice when the actor is important.
- Put conditions before actions.
- Do not omit articles when normal English requires them.
- Do not use semicolons in prose.
- Define abbreviations before repeated use.
- Keep code identifiers, SQL, command names, and PostgreSQL catalog names unchanged.

## Approved technical nouns

The project uses these technical nouns:

- advisory lock
- bounded result
- catalog
- commit state
- connection pool
- cursor
- extension
- failover
- ledger
- migration
- mutation
- OID
- PostgreSQL
- provider profile
- relation
- replication slot
- review hash
- rollback
- row security
- server profile
- transaction
- WAL

## Approved technical verbs

The project uses these technical verbs:

- bind
- classify
- commit
- compose
- encode
- inspect
- reconcile
- roll back
- truncate
- validate

## Automated check

Run this command:

```bash
python scripts/check_ste_docs.py .
```

The checker ignores fenced code, tables, headings, and link targets.

A human review is still required.
