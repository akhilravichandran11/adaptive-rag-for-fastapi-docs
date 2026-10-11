# FastAPI docs adaptive RAG

A retrieval-augmented question-answering system over the FastAPI documentation, built
as a study that compares chunking strategies and embedders on the same corpus.

## Chunk size and shape

**Declared size**:
The minimum and maximum chunk length a chunking strategy states for itself, in its own
unit (words, sentences or characters). A property of the strategy, never of an embedder.
_Avoid_: limit, cap, chunk limit

**Embedder input limit**:
The most tokens an embedder reads from one text; anything past it is ignored when the
text is embedded.
_Avoid_: size, limit (on its own), max length

**Shape rules**:
The checks every chunk is held to whatever the corpus: within its strategy's declared
size (a document shorter than the minimum is exempt), no text lost, in source order,
and not empty.
_Avoid_: size guarantee, chunk validation

**Chunk-size report**:
The counts measured on a chunking run against the shape rules, plus the informational
low-prose and unbroken-run counts. It describes chunks; it never changes them.
_Avoid_: pull report, quality report

**Messy copy**:
A deliberately degraded copy of a real document, standing in for a lower-quality
source the corpus may gain later.
_Avoid_: fuzzed document, corrupted copy

**Low-prose chunk**:
A chunk in which fewer than half the characters are letters or spaces. Informational
only.
_Avoid_: suspect chunk, junk chunk

**Unbroken run**:
A stretch of more than 200 characters with no whitespace, such as an encoded blob.
Informational only.
_Avoid_: long token, blob
