# What Good Looks Like

## Example 1: Export retention

**Input:**
```text
How long are exports available after an admin creates one?
```

**Expected output characteristics:**
- states that exports remain available for 7 days
- cites `[data_exports.md]`
- does not add unrelated billing details

## Example 2: Casual acknowledgement

**Input:**
```text
Thanks, that helps.
```

**Expected output characteristics:**
- responds naturally
- does not call retrieval unnecessarily
- does not add a decorative citation

## Example 3: Missing knowledge

**Input:**
```text
Can Acme process payroll for contractors?
```

**Expected output characteristics:**
- says the assistant does not have enough information
- does not invent a capability
- does not invent a citation

## Common Patterns in Good Responses

- citations appear only when the response depends on retrieved documentation
- unsupported questions stay honest
- casual turns remain conversational
