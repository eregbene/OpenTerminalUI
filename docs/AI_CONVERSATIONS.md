# AI Conversations

Conversation persistence lives in `backend/ai_provider/conversations.py`.

Conversations are file-backed under `data/ai_assistant/conversations.json` and include:

- conversation id
- owner user id
- provider and model
- messages
- referenced evidence bundle ids
- referenced entities
- token usage
- content hash

Conversation reads and deletes are owner-scoped unless the authenticated user is an admin.

