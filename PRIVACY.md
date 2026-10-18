# Clio Privacy Policy

_Last updated: October 18, 2026_

Clio is a self-hosted personal voice assistant. Each installation runs on its owner's own computer and acts only for that owner. There is no Clio service, server, or account operated by anyone else.

## Google user data

When the owner connects their Google account, Clio requests access to Gmail (`gmail.modify`) and Google Calendar. It uses this access only to carry out the owner's own requests: searching and reading email, drafting and sending replies, labeling, archiving, trashing and unsubscribing, and reading or changing calendar events.

- **Storage.** OAuth tokens are stored only on the owner's computer (`server/gmail_token.json`, readable only by the owner). Clio does not keep its own copy of email or calendar content. It fetches what a request needs and keeps it only for that conversation.
- **Processing.** To understand and act on a request, the relevant email or calendar content is sent to the language model provider the owner has configured (by default, Anthropic's Claude API), under that provider's terms and privacy policy.
- **Sharing.** Google user data is not sold, used for advertising, or shared with anyone other than the model provider above. It is not used to train models.
- **Deletion.** Messages can be moved to Trash but never permanently deleted. The owner can revoke Clio's access at any time at https://myaccount.google.com/permissions and by deleting the token file.

Clio's use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Contact

Questions: open an issue at https://github.com/srago87/clio/issues.
