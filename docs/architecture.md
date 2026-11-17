# How a question gets answered

```mermaid
flowchart TD
    subgraph prep["Before a question arrives"]
        chunk["Stage 2 · Chunk the documents"] --> saved[("Saved chunks")]
        saved -->|At API startup| memory["Load chunks into the in-memory BM25 store"]
        saved -->|During indexing| index["Stage 3 · Embed chunks with Voyage (paid)"]
        index -->|Write chunks and vectors| qdrant[("Qdrant collection")]
    end

    api["FastAPI · POST /query"] --> crag["CragOrchestrator.run(question)"]

    subgraph hybrid["Hybrid retrieval"]
        bm25["BM25 over in-memory chunks"] --> fuse["Reciprocal rank fusion"]
        embed["Embed question · Voyage (paid)"] --> dense["Dense search over Qdrant"]
        dense --> fuse
        fuse --> rank["Rerank chunks · Voyage (paid)"]
    end

    memory -.-> bm25
    qdrant -.-> dense
    crag --> bm25
    crag --> embed
    rank -->|Up to 10 chunks| grade{"DocumentGrader · Gemini (paid)<br/>Any relevant chunks? If so, do they cover the question?"}

    grade -->|correct · full coverage| gen["Generate answer from numbered excerpts · Gemini (paid)"]
    grade -->|ambiguous · partial coverage| decompose["Make queries for missing aspects · Gemini (paid)"]
    grade -->|incorrect · no relevant chunks| web["Search the web for the original question · Tavily (paid)"]

    decompose --> retry["Repeat hybrid retrieval for generated retrieval queries · Voyage (paid)"]
    decompose --> gaps["Search the web for generated web queries · Tavily (paid)"]
    retry -->|New chunks| refine["KnowledgeRefinement · Gemini (paid)<br/>Regrade new chunks and extract useful web passages"]
    gaps --> refine
    web -->|Web pages only| refine
    refine -->|Kept evidence| gen
    gen --> response["API response with answer, sources and route"]
```

On the correct route, the generator receives the relevant chunks directly. On the
ambiguous route, refinement keeps those chunks and adds evidence from the new
searches. If initial retrieval is empty or a route yields no context, the
orchestrator returns a fixed answer with empty sources and skips generation.
