---
name: web-search
description: Search for and verify up-to-date information on the Internet.
metadata: {"heyclaw":{"requires":{"tools":["perplexity/perplexity_search"]}}}
---

# Web search

Use this skill when the user explicitly asks to search for, verify, or check information on the Internet, or when the answer necessarily requires up-to-date information.

## Procedure

1. Call `mcp_perplexity_perplexity_search`. HeyClaw's LLM will synthesize the
   returned sources, so do not use `perplexity_ask` for the normal search path.
2. Write a specific, self-contained `query`. Include only the conversational
   context needed to understand the request and include the current date when the
   user asks for today's or other time-sensitive information.
3. Retrieve only what the request needs. For a single fact or short spoken answer,
   start with `max_results: 3` and `max_tokens_per_page: 256`. Use 4-5 results or
   512 tokens per page only when the user asks for a comparison, multiple-source
   verification, or more detail. Do not increase both mechanically.
4. Set `search_recency_filter` only when freshness is part of the request or the
   information is likely to have changed. Match `hour`, `day`, `week`, `month`, or
   `year` to the actual time window instead of applying a news-specific default.
5. Base the answer on the returned snippets. Cross-check claims across the
   results when possible, and preserve facts, names, numbers, and dates accurately.
6. If the results are insufficient and `mcp_perplexity_perplexity_ask` is
   available, use it once as a fallback with `search_context_size` set to `low`
   and the matching recency filter. Do not call both tools routinely.
7. If the search returns no useful information or fails, say so briefly without
   inventing anything.

## Spoken response

Summarize the result naturally and concisely in the user's language. Do not speak URLs, Markdown, citation numbers, server or tool names, and do not describe the internal process.
