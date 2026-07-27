# ASD-STE100 project profile

Repository documentation uses a project profile based on ASD-STE100 Issue 9 principles.

This project does not reproduce the copyrighted standard or its controlled dictionary.

Use the official standard as the authority for a formal compliance review.

## Scope

The profile applies to these repository surfaces:

- Markdown headings, paragraphs, lists, quotes, and tables.
- MCP tool descriptions.
- Pydantic field descriptions.
- Command-line descriptions and help text.
- GitHub workflow names and input descriptions.
- Public package metadata.

Code identifiers, SQL, URLs, and command examples keep their required syntax.

## Project rules

- Use one approved meaning for each word.
- Use short sentences.
- Use no more than 25 words in a descriptive sentence.
- Use one instruction in each procedural sentence.
- Use active voice when the actor is important.
- Put conditions before actions.
- Use articles when normal English requires them.
- Use `and` instead of an ampersand in prose.
- Do not use semicolons in prose.
- Define an abbreviation before repeated use.
- Keep technical identifiers unchanged.

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
- object identifier (OID)
- pgvector
- PostGIS
- PostgreSQL
- provider profile
- relation
- replication slot
- review hash
- rollback
- row security
- server profile
- transaction
- write-ahead log (WAL)

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

The checker examines all scoped repository text.

The checker ignores fenced code, link targets, URLs, image markup, and technical identifiers.

A human review must confirm vocabulary, meaning, and sentence structure.
