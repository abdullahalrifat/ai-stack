# Changelog

## 3.7.0

- Clarify the AI Stack control-plane API boundary: AI Stack routes are rooted at `/`, not `/v1`.
- Document that `/v1` is reserved for the private jarvis-inference OpenAI-compatible gateway.
- Align the Jarvis integration with the authenticated `/models/available` AI Stack model catalog.
- Bump the FastAPI service version from 3.6 to 3.7.
