# GPT Image 2 adapter

Adapter command:

```json
{"command": ["python3", "/absolute/path/provider_adapters.py", "gpt-image-2"], "timeout_seconds": 600}
```

Required environment: `OPENAI_API_KEY`. `OPENAI_BASE_URL` optionally overrides the default
`https://api.openai.com/v1` and must remain HTTPS.

Each user configures their own key. The blank template that `provider_adapters.py --write-config`
copies to `~/.drama-skills/providers.env` (or the file named by `DRAMA_SKILLS_ENV`) already points
`OPENAI_BASE_URL` at the yoyo relay, `https://api.yoyochatai.com/v1`, which serves both
`/images/generations` and `/images/edits` for `gpt-image-2`; the user pastes their own relay key into
`OPENAI_API_KEY`. Another OpenAI-compatible relay works the same way by changing that one line.
The file must sit outside every project; values already in the process environment win, so a
system-wide `OPENAI_API_KEY` for another service overrides the file and has to be removed or matched.

The job must use modality `image` and have exactly one output. With no references, the adapter sends JSON to
`POST /images/generations`. With one to sixteen references, it sends an `image[]` multipart edit to
`POST /images/edits`. The model is always `gpt-image-2`, `n` is always one, and the single returned `b64_json` image is
written to a private temporary file.

Supported public parameters are `width` plus `height` (compiled to `size`), or `size`, and `quality`, `background`, and
`moderation`. References use GPT Image 2's high-fidelity behavior without sending the older `input_fidelity` field.
Transparent backgrounds are rejected because GPT Image 2 does not support them. The output extension selects `png`,
`jpeg`, or `webp`.

Protocol references: [GPT Image 2 model](https://developers.openai.com/api/docs/models/gpt-image-2),
[image generation](https://developers.openai.com/api/reference/resources/images/methods/generate), and
[image edits](https://developers.openai.com/api/reference/resources/images/methods/edit).
