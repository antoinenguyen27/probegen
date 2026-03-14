# Production Traces

Place anonymised production conversation samples here as .txt or .json files.

**Important:** Ensure all traces are anonymised before committing. Remove names, email 
addresses, account IDs, and any other personally identifiable information.

## .txt format
One conversation per file. Format:
```
USER: [message]
ASSISTANT: [response]
USER: [follow-up]
ASSISTANT: [response]
```

## .json format
Array of message objects:
```json
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```
