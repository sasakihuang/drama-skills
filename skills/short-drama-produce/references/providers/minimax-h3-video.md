# MiniMax video adapter (H3, H3-Max, Hailuo)

Adapter command, one profile per model (`provider_adapters.py --write-config` writes all of them with
absolute paths into `~/.drama-skills/adapters.json`):

```json
{"command": ["python3", "/absolute/path/provider_adapters.py", "minimax-video", "--model", "MiniMax-H3"], "timeout_seconds": 3600}
```

`minimax-h3` without `--model` remains as the original form and reads `MINIMAX_VIDEO_MODEL`.

## Built-in models and official prices

`--model` accepts the five catalog entries below. For them the resolution set and duration range
come from the published envelope, so the `MINIMAX_VIDEO_*` profile variables are optional (when set,
they still narrow the profile). Any other model ID is passed through the H3 path and still needs the
explicit profile described under "Environment".

| Model | Profile | API | Modes | Duration | Official price (CNY) |
|---|---|---|---|---|---|
| MiniMax-H3 | `minimax-h3` | v2 | text, first/last frame, multi-reference | 4–15 s | 768P 0.50/s; 2K 0.80/s |
| MiniMax-H3-Max | `minimax-h3-max` | v2 | text, first/last frame, multi-reference | 5–15 s | 480P 0.33/s; 768P 0.50/s |
| MiniMax-Hailuo-2.3-Fast | `minimax-hailuo-2.3-fast` | v1 | first frame only | 6 or 10 s | 768P 6 s 1.35, 10 s 2.25; 1080P 6 s 2.31 |
| MiniMax-Hailuo-2.3 | `minimax-hailuo-2.3` | v1 | text, first frame | 6 or 10 s | 768P 6 s 2.00, 10 s 4.00; 1080P 6 s 3.50 |
| MiniMax-Hailuo-02 | `minimax-hailuo-02` | v1 | text, first frame, first+last frame | 6 or 10 s | 512P 6 s 0.60, 10 s 1.00 (first frame only); 768P 6 s 2.00, 10 s 4.00; 1080P 6 s 3.50 |

Prices are the China-site pay-as-you-go list, checked on the date stored in
`MINIMAX_PRICE_CHECKED`. A subscription plan is drawn down at these same prices and plans differ in
what they include, so the creator is shown prices only, never a plan allowance:

- `provider_adapters.py --price-table` prints this table in Chinese for the creator.
- `provider_adapters.py --quote <job.json> ...` prices prepared video jobs. The model comes from
  `--model`, or from the job's `adapter` profile name as written by `--write-config`, or from
  `--model` inside that profile's command in `--adapter-config`.

## Environment

Required:

- `MINIMAX_API_KEY`: MiniMax API key. A subscription key normally starts with `sk-cp-`; a
  pay-as-you-go key bills the account balance directly. `--check-config` reports which kind is set
  without printing it.

Optional:

- `MINIMAX_REGION`: `cn` (default, `https://api.minimaxi.com`) or `global`
  (`https://api.minimax.io`). A key only works on the site that issued it.
- `MINIMAX_VIDEO_BASE_URL` (H3 path, default `<region host>/v2`) and `MINIMAX_BASE_URL` (Hailuo,
  speech and music, default `<region host>/v1`) override the region host.
- `MINIMAX_AIGC_WATERMARK`: `true` by default, adding MiniMax's visible AI-generated label to the
  video. Generated video published in China must carry an explicit AI label; set `false` only when
  the label is added another way.
- `MINIMAX_VIDEO_MODEL`: the model for the plain `minimax-h3` form.
- `MINIMAX_VIDEO_RESOLUTIONS`: comma-separated subset the configured model actually accepts, drawn
  from `480P`, `768P`, `2K`. Required for a model outside the catalog.
- `MINIMAX_VIDEO_MIN_DURATION` and `MINIMAX_VIDEO_MAX_DURATION`: inclusive duration range in whole
  seconds. Set both or neither; required for a model outside the catalog.
- `MINIMAX_VIDEO_RATIOS`: comma-separated subset of `adaptive`, `1:1`, `3:4`, `4:3`, `9:16`, `16:9`,
  `21:9`.
- `MINIMAX_VIDEO_POLL_INTERVAL` (default `5` seconds)
- `MINIMAX_VIDEO_TIMEOUT_SECONDS` (default `1800` seconds)

Any of these may instead be written in the user's `~/.drama-skills/providers.env` (or the file named
by `DRAMA_SKILLS_ENV`), one `NAME=value` per line. The file must sit outside every project, values
already in the process environment win, and its contents are never printed or echoed in errors.

## Hailuo (v1) jobs

The Hailuo models use the older endpoint: `POST {v1}/video_generation`, then
`GET {v1}/query/video_generation?task_id=` until `Success` or `Fail`, then
`GET {v1}/files/retrieve?file_id=` for the download URL. The handle is recorded before the first
poll, so `collect` resumes a submitted task exactly as on the H3 path.

- References: only `first_frame` (sent as `first_frame_image`) and, on Hailuo-02, `last_frame`
  (`last_frame_image`, which needs a first frame and is not offered at 512P). Any other role fails
  closed. Local frames are sent inline as data URIs, each under 20MB.
- No reference contract is appended: there is nothing in the request to point at by name, and the
  prompt is limited to 2000 characters. Camera moves can use the bracket commands described in the
  Hailuo dialect of `$short-drama-video-prompts`.
- `duration` and `resolution` must be a combination in the price table. `ratio` is optional and not
  sent: the first frame decides the shape, and text-to-video only renders 16:9, so any other ratio
  on a text-to-video job is refused.
- `prompt_optimizer` is sent as `false` unless the job sets it to `true`; the accepted prompt is
  used as written.
- The output has no audio track; sound is added in the edit.

## H3 (v2) jobs

The job must have modality `video` and exactly one `.mp4` output. `duration` and `resolution` are
required and are only accepted when the runtime profile above permits their values. `ratio` is
optional for reference-conditioned jobs and **required** for text-to-video, where `adaptive` is
refused because there is no reference frame to adapt to.

The adapter validates and forwards `duration`; it does not estimate speech length or retime dialogue.
Before preparing a speaking shot, compare the accepted shot duration, the prompt's spoken timeline,
and the job's `duration`. Resolve insufficient speaking time in the storyboard and regenerate the
dependent prompt before preparing the job. Prompt timestamps alone do not change the request duration.
The official video API has no separate speech-rate parameter.

The prompt is compiled into one `text` item of the multimodal `content` array and is refused above
7000 characters. Each declared reference becomes one further `content` item carrying an explicit
`role`: `first_frame`, `last_frame`, `reference_image`, `reference_video`, or `reference_audio`.
Set the job's `parameters.prompt_language` to the resolved video-prompt language; the compiler
consumes it when appending reference semantics and does not send it as a MiniMax request field.
`first_frame` and `last_frame` may each appear once. The published envelope also caps reference
conditioning at 9 `reference_image`, 3 `reference_video` and 3 `reference_audio` items, with video and
audio clips between 2 and 15 seconds each and at most 15 seconds in total per modality; the compiler
does not count them, so a configuration that can exceed those numbers has to bound them itself.
frame conditioning (`first_frame` / `last_frame`) and full-reference conditioning
(`reference_image` / `reference_video` / `reference_audio`) are mutually exclusive in one request.
For continuation, bind the previous actual video as `reference_video` and its actual tail as
`reference_image`; the compiler labels them `<Video 1>` and `<Picture 1>` in its appended contract.
This describes compiler support. The current creator-first source selector cannot declare that video,
so continuation remains unsubmitted in the bundled workflow; see the [source-interface boundary](../adapter-contract.md).
Never relabel that tail as `first_frame` while a reference video is present. The compiler accepts HTTPS, `mm_file://{file_id}`, or `data:<mime>;base64,<...>` reference URLs.
The bundled runtime reads each confirmed project-relative reference from disk and sends it
inline as a data URI, so binding an image in the creator documents is enough to run the job; no
upload service is required. Each file's bytes must match the media type its extension claims, and
the published per-modality caps apply: 30MB per image, 50MB per video, 15MB per audio clip, and 64MB
for the provider request. The local guard checks individual files and the combined base64 reference
strings against these caps; other request fields also contribute to the provider's body limit.
An over-limit reference fails before submission. This production tool accepts project files,
so a hosted URL cannot replace its `references` path. Have the responsible skill prepare a suitable
input, preserve the accepted content and control scopes, then preview and confirm the updated job.

Each reference takes its provider role from the confirmed job's `reference_bindings[].role`, which is
where the creator document's `用途` is translated for this provider. A job that carries references
without those bindings fails closed rather than guessing a role.

The adapter creates an asynchronous task with `POST {base}/video_generation`, polls
`GET {base}/query/video_generation/{task_id}` until a terminal state, and downloads `task.content.url` into a private temporary directory. Any unknown
status fails closed.

## What this model changes for the prompt itself

This release generates audio in the same pass as the picture. Its direct prompt dialect is also
structured: base/first-frame/first-last-frame tasks use `integrated_multimodal_description`,
`overall_soundscape`, and `non_diegetic_music`; full-reference tasks use the six-section H3 reference
form. Chinese dialogue remains exact inside `<d>[Chinese] ...</d>`. Use
`$short-drama-video-prompts` for the full H3 prompt dialect.

State the intended sound layers in the prompt. For no non-diegetic music, the
H3 dialect uses `non_diegetic_music: N/A`; small probes observed unwanted music
when this layer was omitted. They did not establish that omitted speech or
negative clauses cause extra dialogue.

Follow the project's subtitle and in-scene-text policy. Prompt wording belongs
to the video-prompt skill and the declared language, not to adapter-generated
boilerplate.

Protocol references: [MiniMax video generation API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create),
[Hailuo image-to-video](https://platform.minimaxi.com/docs/api-reference/video-generation-i2v),
[Hailuo first and last frame](https://platform.minimaxi.com/docs/api-reference/video-generation-fl2v),
[official prices](https://platform.minimax.cn/docs/guides/pricing-paygo).
