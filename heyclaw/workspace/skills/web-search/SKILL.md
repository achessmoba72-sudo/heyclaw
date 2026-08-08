---
name: web-search
description: Search for and verify up-to-date information on the Internet.
metadata: {"heyclaw":{"requires":{"tools":["perplexity/perplexity_ask"]}}}
---

# Web search

Use this skill when the user explicitly asks to search for, verify, or check information on the Internet, or when the answer necessarily requires up-to-date information.

## Procedure

1. Call `mcp_perplexity_perplexity_ask`.
2. Pass the current request and only the conversational context needed to understand it in the `messages` parameter.
3. Base the answer on the data returned by the tool. Preserve facts, names, numbers, and dates accurately.
4. If the tool does not return the requested information or fails, say so briefly without inventing anything.

## Spoken response

Summarize the result in natural, concise English. Do not speak URLs, Markdown, citation numbers, server or tool names, and do not describe the internal process.
