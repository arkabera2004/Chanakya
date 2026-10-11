# Google Flow prompts for Chanakya and Vira

Prompts for Google Flow that produce one short product film (about 60 seconds), one loopable
ambient background, and a set of stills for the web app. Every clip should look like the same
film, and Vira should be the same character in every shot.

The look comes from the app itself: the orb in `web/src/components/vira/orb.tsx` and the tokens in
`web/src/styles/tokens.css`. If the orb changes there, update the character block here.

Flow facts in this file were checked against Google's own pages in October 2026. Flow changes
often. Where something could not be confirmed, the file says so. Sources are listed at the end.

## 0. Stills already generated

The stills in this file were generated once in Paper (Nano Banana 2.1) on 2026-10-10 and saved in
`docs/brand/flow-stills/`: the four Vira moods (`A1` to `A4`), a start frame for shots 2 to 9, and
the hero and share card plates (`D2`, `D3`). Upload the Vira stills to Flow as ingredients and the
shot stills as start frames, so there is no need to generate them again. Flow still has to make
the video clips. Two things to know: the wide frames were made from a 16:9 plate of each mood
still, because the image model otherwise returns a square; and the hero plate was cropped by hand
to remove a black band the model kept adding at the top.

## 1. Rules that do not bend

These are project policy. They apply to every prompt, every still, and the final cut.

- Never show a real tender, a real portal screenshot, a ministry name, a flag, or a government
  emblem. Chanakya never presents a synthetic notice as real, and the film must not either.
- No human faces. No people at all is the safe default.
- Vira is the orb. Never a robot, never a person, never a mascot with limbs or a mouth.
- The model draws no text, no numbers, and no interface. Every sheet, panel, and screen in a
  generated shot is blank.
- Real interface footage comes from screen-recording the app on demo data (start the API with
  `CHANAKYA_CONFIG=config/chanakya.demo.yaml chanakya serve`), where every record
  is labelled synthetic. Rupee figures, deadlines, and scores appear only in those recordings.
- Titles and captions are added in the edit: Michroma for titles, Geist for body text.

## 2. What Flow can do, and what that means here

| Fact | What we do about it |
| --- | --- |
| Google's prompt formula for Veo 3.1 is Cinematography, Subject, Action, Context, Style and ambiance. | Every shot prompt below follows that order. |
| Audio is prompted in the text: dialogue in quotation marks, `SFX:` for sound effects, `Ambient noise:` for the soundscape. | Every shot has an `Ambient noise:` line and an `SFX:` line. No dialogue anywhere. |
| Timestamps such as `[00:00-00:02]` set the pacing inside one generation. Google shows this for multi-shot sequences. | We use them for timing beats inside one continuous shot. If a clip cuts between beats, see troubleshooting. |
| Up to three ingredients (reference images) per prompt. Subject references work best on a plain or segmented background. | The Vira reference still is always ingredient one, on a plain dark background. |
| In Flow, ingredients run on Veo 3.1 Fast and Veo 3.1 Lite at 8 seconds only. Veo 3.1 Quality does not take ingredients. Omni Flash takes ingredients at 4 to 10 seconds. | Vira shots are built from a start frame instead (see section 3), which works on every model. |
| Frames to video accepts a start frame, or a start and an end frame, on every listed model, at 4, 6, or 8 seconds, in 16:9 or 9:16. | This is the main method for every shot with Vira in it. |
| Any frame of a clip can be saved (pause, hover, Save frame) and reused as an ingredient, a start frame, or an end frame. | This is how shots are chained. |
| Extend works only on Veo clips of 8 seconds, and must be run with Veo 3.1 Lite. Insert, remove, and camera edits cannot be applied to an extended clip. | Extend is a last resort. Do any insert, remove, or camera edit first. |
| Google's Vertex guide says to avoid words such as "no" and "don't", and to name the unwanted things instead. Google's Veo 3.1 guide says to describe the scene you want. | Prompts describe blank surfaces positively, then end with an `Avoid:` list of plain nouns. |
| Ingredient images, frames, and prompt text should agree with each other and share one style. | All stills are generated with the same style block before any video is made. |

## 3. Workflow

### Project settings

- Aspect ratio: 16:9 for the film. Keep it for every still and clip in the project.
- Clip length: 8 seconds. Trim in the edit.
- Outputs per prompt: 2 to 4, so there is a choice.
- Model: Veo 3.1 Quality for final clips made from frames. Veo 3.1 Fast for cheap test runs and
  for any shot made with ingredients.
- Stills: Flow's image model (the help page lists Nano Banana Pro as the default).

### Order of work

1. Generate the Vira reference still (prompt A1). Make many, pick one. Compare it side by side
   with the live orb in the app. Save the winner to the project and name it `Vira neutral`.
2. Make the three mood stills (A2 to A4) by editing `Vira neutral`, not by generating from
   scratch. Save each one.
3. For each shot with Vira in it, generate a start frame as a still: paste the shot's "Start
   frame" prompt into the image model with the right Vira mood still attached as an ingredient.
   A still is quick to judge. Reject any frame where the orb is off-model.
4. Animate each approved start frame with Frames to video, using the shot prompt. Add an end
   frame only where the shot notes ask for one.
5. Chain shots that share a space (2, 3, 4 and 5, 6, 7): open the finished clip, pause on the last
   clean frame, Save frame, and use it as an ingredient when making the next shot's start frame so
   the space and light carry over.
6. Add the picks to a scene in Scenebuilder, put them in order, trim the handles, and preview.
7. Download, then finish in a video editor: cut in the demo data recordings, add titles, music,
   and the voiceover.

### How to paste the blocks

Each shot prompt ends with two placeholder lines, `{CHARACTER}` and `{STYLE}`. Replace each one
with the full text of the matching block from section 4. Paste them unchanged every time. Small
rewording between shots is one of the main causes of drift. Shots with no Vira in them have only
`{STYLE}`.

### When Vira drifts off-model

Check in this order and stop at the first fix that works.

1. Fix the start frame, not the clip. If the orb is wrong in frame one, it stays wrong.
2. Re-attach `Vira neutral` (or the mood still) as the first ingredient and regenerate the start
   frame.
3. Shorten the action. One clear action per clip holds better than three.
4. Make the orb larger in frame. Small subjects lose detail first, and the eyes go first of all.
5. Add an end frame that also shows a correct Vira, so the model has a target at both ends.
6. Switch the shot to ingredients on Veo 3.1 Fast with only the Vira still attached.
7. If the body is right and only the eyes are wrong, generate the shot with neutral eyes and cut
   on the expression change, or composite the live orb from the app over a clean plate.

## 4. The two shared blocks

### Style block

Paste in place of `{STYLE}` in every prompt, stills and clips alike.

```
Style: calm, minimal, premium product film with a clean 3D motion-design look. One continuous shot with no cuts. Near-black charcoal background with deep blue-black surfaces. The only saturated colour allowed is a warm orange glow, and everything else is neutral grey or pale off-white. Low-key lighting, soft volumetric glow, gentle falloff into darkness, fine film grain, slow deliberate movement, generous empty dark space. Every sheet, panel, screen, and surface is blank and unmarked.
Avoid: text, letters, numbers, captions, subtitles, logos, watermarks, user interface, charts, icons, flags, emblems, seals, maps, buildings, people, faces, hands, robots, mouths, speech, singing, music.
```

### Character block

Paste in place of `{CHARACTER}` in every prompt that has Vira in it. The description matches
`orb.tsx`: the four-stop gradient, the highlight at the upper left, the ink shading at the bottom,
the halo, and two white round-capped eyes set a little above the middle.

```
Character: Vira, the orb in the attached reference image, matched exactly in shape, colour, and eyes. A soft, matte, self-lit orb, almost a perfect sphere, with a very slightly uneven outline like a drop of warm liquid. Warm amber at the upper left, deepening through orange to deep orange-red at the lower right edge, with a soft pale highlight on the upper left, a slightly darker underside, and a faint orange halo on the darkness around it. On the front, a little above the middle, are two pure white eyes: short upright bars with fully rounded ends, about twice as tall as wide, side by side with a clear gap between them. The eyes are the only features on a smooth blank surface. The orb floats without support, bobs very slightly, breathes with a barely visible swell, and blinks by squashing both eyes flat for an instant.
```

Colour is written in words in the video blocks, and hex codes appear only in the still prompts.
This is a precaution, not a confirmed rule: no Google source says Veo honours hex codes, and the
reference image carries the exact colour anyway.

## 5. A. Vira reference stills (image model, 1:1)

### A1. Neutral, the master reference

```
A single glowing orb character, centred, on a plain flat near-black background (#101010). Front view, eye level, orb fills about half the frame height, nothing else in the image.
The orb is a soft matte sphere with a very slightly uneven outline, like a drop of warm liquid light. It is filled with a smooth radial gradient whose brightest point sits at the upper left: amber (#FFA12B) at the highlight, through #FA8D23 and orange (#F0731A), to deep orange-red (#DC440C) at the lower right edge. The lower third is shaded slightly darker with a blue-black tint (#161C26). A thin pale rim of light follows the top edge, and a soft blurred white highlight sits on the upper left. A faint soft orange halo spreads a short way onto the background.
On the front, a little above the middle, are two pure white (#FFFFFF) eyes. Each eye is a short upright bar with fully rounded ends, about twice as tall as wide, about one fifth of the orb's height. The two eyes are identical, level with each other, and the distance between their centres is a little under one third of the orb's width. The eyes are the only features on a smooth blank surface.
Flat soft shading, clean vector-like 3D render, even exposure, sharp focus on the whole orb.
Avoid: mouth, nose, eyebrows, pupils, eyelashes, arms, legs, robot parts, glass reflections, text, logos, watermarks, people, faces.
```

### A2 to A4. Mood variants

Attach `Vira neutral` and edit it. Use the same opening and closing sentences each time and swap
only the eye description.

```
Keep everything in the attached image identical: the orb's shape, size, position, colours, highlight, halo, and background. Change only the two white eyes. {EYES} The eyes stay pure white with fully rounded ends and stay in the same position on the orb.
```

Replace `{EYES}` with one of these.

| Mood | Used when | `{EYES}` text | Values in `orb.tsx` |
| --- | --- | --- | --- |
| neutral | Default, resting | Two identical short upright bars, about twice as tall as wide, level with each other. | length 15, upright, weight 12 |
| happy | A result is ready | Each eye becomes a thin smooth arch that curves upward in the middle with both ends pointing down, like a closed smiling eye. The arches are about half as thick as the original bars, about as wide as the original bars were tall, and sit very slightly lower. | length 17, horizontal, bent upward by 10, weight 6.5, lowered by 2 |
| curious | Looking at something new | The eye on the left of the image becomes shorter and sits a little lower. The eye on the right of the image becomes clearly taller, slightly thicker, and sits a little higher. Both stay upright. | left length 11, lowered by 2. Right length 19, weight 13, raised by 2 |
| focused | Reading and judging | Both eyes become short stubby bars, about three quarters of their original height, each tilted about 20 degrees so the tops lean away from each other and the bottoms lean toward each other, forming a shallow V. They sit very slightly lower. | length 9, tilted 20 degrees off upright in opposite directions, weight 12, lowered by 1 |

Save the four results as `Vira neutral`, `Vira happy`, `Vira curious`, `Vira focused`.

## 6. B. The film (video, 16:9, 8 seconds per clip)

### The cut

Nine generated shots plus one real screen recording. Each clip is generated at 8 seconds and
trimmed to the length below.

| # | Shot | Vira mood | In the cut | Running time |
| --- | --- | --- | --- | --- |
| 1 | The flood | none | 6 s | 0:00 to 0:06 |
| 2 | Vira wakes | neutral | 5 s | 0:06 to 0:11 |
| 3 | Sources open | neutral | 7 s | 0:11 to 0:18 |
| 4 | The closed source | curious | 5 s | 0:18 to 0:23 |
| 5 | Most are set aside | focused | 7 s | 0:23 to 0:30 |
| 6 | The evidence line | focused | 6 s | 0:30 to 0:36 |
| R | Demo data screen recording | real app | 6 s | 0:36 to 0:42 |
| 7 | The short list | happy | 6 s | 0:42 to 0:48 |
| 8 | Morning hand-off | neutral | 6 s | 0:48 to 0:54 |
| 9 | End card plate | neutral | 6 s | 0:54 to 1:00 |

Each shot has one main action. The timestamps describe the phases of that one action, not
separate events.

### Shot 1. The flood

Mode: text to video. Model: Veo 3.1 Quality. Ingredients: none.

```
Cinematography: wide shot, wide-angle lens, eye level. The camera dollies forward very slowly in a straight line. Deep space, with the nearest sheets soft and out of focus.
Subject and action: thousands of thin, blank, pale off-white paper sheets drift slowly toward the camera through a vast dark space, like snow falling through deep water. They tumble gently and never touch.
[00:00-00:03] Scattered sheets drift out of the darkness in the distance.
[00:03-00:06] The sheets grow denser. A few pass close to the lens as large soft blurs.
[00:06-00:08] Sheets fill most of the frame, uncountable, still drifting.
Setting: an empty, endless dark space with no floor, walls, or horizon.
Lighting: low-key, a faint cool grey light from above catches the edges of the sheets. The background stays near-black.
Ambient noise: a deep, quiet room tone and a soft continuous rustle of paper, like distant rain.
SFX: single sheets whisper past close to the camera.
{STYLE}
```

### Shot 2. Vira wakes

Mode: frames to video. Model: Veo 3.1 Quality. End frame: optional, a still of Vira neutral,
centred, with sheets hanging still around it.

Start frame (image model, no ingredient):

```
Wide shot of a vast dark empty space filled with blank pale off-white paper sheets drifting at different depths, the nearest ones out of focus. The centre of the frame is clear dark space with one very small, dim point of warm orange light.
{STYLE}
```

Clip:

```
Cinematography: medium wide shot, standard lens, eye level. The camera is almost static, with a very slow push in toward the centre of the frame. Shallow depth of field, with the sheets nearest the lens out of focus.
Subject and action: Vira fades in at the centre of the dark space and wakes.
[00:00-00:02] The small point of warm orange light at the centre grows brighter.
[00:02-00:05] The light swells into Vira. Its eyes are closed, two thin flat white lines.
[00:05-00:06] The eyes open into short upright bars.
[00:06-00:08] Vira blinks once and settles into a slow, gentle bob. The paper sheets around it slow down until they hang almost still, their nearest edges catching a little orange light.
Setting: the same endless dark space as the opening, full of drifting blank pale sheets.
Lighting: low-key. Vira is the only light source, casting a soft warm glow on the closest sheets, with the rest falling into darkness.
Ambient noise: the paper rustle fades to near silence, leaving a deep quiet room tone.
SFX: a low, warm hum swells gently as the light grows, then settles.
{CHARACTER}
{STYLE}
```

### Shot 3. Sources open

Mode: frames to video. Model: Veo 3.1 Quality. Ingredients for the start frame: `Vira neutral`,
plus the saved last frame of shot 2 for the lighting.

Start frame (image model):

```
Wide shot, eye level. Four tall, narrow panels of dark smoked glass stand upright in a straight row across the frame, evenly spaced, receding slightly to the right. All four are unlit and blank. Vira, the orb from the attached reference image, floats at mid height at the left edge of the row, small in frame, with neutral eyes. Dark floor with a faint reflection of the orb's glow.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: wide shot, standard lens, eye level. The camera trucks slowly to the right, parallel to the row of panels, keeping pace with Vira. Deep focus.
Subject and action: Vira floats along a row of four tall dark glass panels, and the panels light up from within as it reaches them.
[00:00-00:03] Vira drifts to the first panel. The panel fills with a soft, even, pale glow from inside, like a doorway opening onto a lit room.
[00:03-00:06] Vira moves on. The second panel lights the same way, then the third.
[00:06-00:08] Vira slows as it nears the fourth panel, which stays completely dark.
Setting: a dark, empty hall with a smooth dark floor that faintly reflects the light.
Lighting: low-key. The lit panels give a soft pale off-white light. Vira gives a warm orange glow. The fourth panel and the far background stay near-black.
Ambient noise: a quiet, spacious room tone.
SFX: a soft, low, rounded tone as each panel lights, three in total.
{CHARACTER}
{STYLE}
```

### Shot 4. The closed source

This shot carries a product promise: Vira does not force a source that is closed. The shutter must
stay shut for the whole clip. Reject any take where it opens, cracks, or glows.

Mode: frames to video with a start frame and an end frame. Model: Veo 3.1 Quality. Ingredients
for the start frame: `Vira curious`, plus the saved last frame of shot 3. End frame: the same
composition with the shutter unchanged and Vira gone from frame.

Start frame (image model):

```
Medium close-up, eye level. One tall, narrow panel fills the right half of the frame. It is covered by a closed, matte, dark grey shutter made of plain horizontal slats, completely unlit. Vira, the orb from the attached reference image, floats at the left of the frame at mid height, facing the shutter with a clear gap between them. Its eye on the left is short and a little lower, and its eye on the right is taller and a little higher. Far in the background at the left, three soft pale lit panels are out of focus.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: medium close-up, telephoto lens, eye level. The camera is locked off and does not move. Shallow depth of field, with the lit panels far behind as soft pale blurs.
Subject and action: Vira looks at a closed shutter, waits, and leaves it closed.
[00:00-00:03] Vira hovers in front of the shuttered panel, keeping a clear gap. Its eyes are uneven, one short and low, one tall and high, as it studies the shutter.
[00:03-00:05] Vira holds still. The shutter stays shut, dark, and unchanged.
[00:05-00:08] Vira's eyes return to two matching upright bars. It drifts backward and glides out of the left side of the frame, toward the lit panels. The shutter remains closed and dark.
Setting: the same dark hall, at the fourth panel in the row.
Lighting: low-key. Vira's warm glow falls softly on the matte slats and fades as Vira leaves. No light comes from the panel itself.
Ambient noise: a quiet, spacious room tone.
SFX: near silence, with only a faint low hum from Vira that fades as it leaves.
{CHARACTER}
{STYLE}
```

### Shot 5. Most are set aside

Mode: frames to video. Model: Veo 3.1 Quality. Ingredients for the start frame: `Vira focused`.

Start frame (image model):

```
Medium wide shot, eye level, side-on. Vira, the orb from the attached reference image, floats at the centre of the frame. Its eyes are two short stubby white bars tilted into a shallow V, tops apart and bottoms closer. A loose stream of blank pale off-white paper sheets enters from the left edge of the frame at the orb's height. Empty dark space above, below, and to the right.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: medium wide shot, standard lens, eye level, side-on. The camera is locked off and does not move. Moderate depth of field with Vira sharp.
Subject and action: a stream of blank paper sheets flows past Vira from left to right. Vira keeps a few and lets the rest go.
[00:00-00:02] The stream of pale sheets reaches Vira and flows past it at a steady pace.
[00:02-00:06] Most sheets lose their brightness as they pass, turn dull grey, and sink gently out of the bottom of the frame. A few sheets catch Vira's orange light along their edges, brighten, and rise to hover at Vira's right.
[00:06-00:08] The stream thins to nothing. A small loose group of softly glowing sheets hovers beside Vira.
Setting: an empty, endless dark space.
Lighting: low-key. Vira is the key light. Kept sheets glow warm along their edges. Set-aside sheets fall into shadow.
Ambient noise: a steady, soft rustle of paper that thins out toward the end.
SFX: a soft, airy sigh as sheets sink away. A faint warm tone each time a sheet is kept.
{CHARACTER}
{STYLE}
```

### Shot 6. The evidence line

Mode: frames to video. Model: Veo 3.1 Quality. Ingredients for the start frame: `Vira focused`,
plus the saved last frame of shot 5.

Start frame (image model):

```
Close-up, eye level. One blank, smooth, pale off-white paper sheet hovers upright in the left two thirds of the frame, turned slightly toward the camera, softly lit and softly glowing at its edges. Behind it at the right, slightly out of focus, floats Vira, the orb from the attached reference image, with eyes as two short stubby white bars tilted into a shallow V. Dark empty background.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: close-up, macro lens, eye level. The camera pushes in very slowly toward the sheet. Very shallow depth of field, with the sheet sharp and Vira soft behind it.
Subject and action: a thin line of orange light appears across one band of a blank sheet and stays lit, as Vira points to the reason for its decision.
[00:00-00:02] The blank sheet hovers, almost still. Vira floats behind it.
[00:02-00:05] A thin, straight, horizontal line of orange light draws itself from left to right across the sheet, about one third of the way down, and stays glowing.
[00:05-00:08] Vira leans a little closer and dips once, like a small nod. The line keeps glowing.
Setting: an empty dark space, with a few softly glowing sheets far behind as blurs.
Lighting: low-key. The orange line lights the paper around it. Vira's warm glow rims the edge of the sheet.
Ambient noise: a deep, quiet room tone.
SFX: a soft, dry hiss as the line of light draws across the sheet, then a gentle low tone as Vira nods.
{CHARACTER}
{STYLE}
```

### Shot R. The real evidence line

Not generated. Screen-record the app on demo data, showing one synthetic notice with its
evidence line highlighted. Cut to it straight after shot 6 so the abstract line and the real line
land together. This is the only place in the film where words and numbers appear.

### Shot 7. The short list

Mode: frames to video. Model: Veo 3.1 Quality. Ingredients for the start frame: `Vira neutral`.
The eyes change to happy during the clip. If they will not change cleanly, add an end frame made
with `Vira happy`.

Start frame (image model):

```
Medium shot, eye level. Vira, the orb from the attached reference image, floats at the right of centre with neutral eyes. In front of it and to the left, a few softly glowing blank pale sheets hover in a loose group, about to settle. Far behind, a great many dim grey sheets hang in the darkness, out of focus.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: medium shot, standard lens, eye level. The camera dollies backward slowly to reveal more space. Moderate depth of field.
Subject and action: a few glowing sheets settle into a neat stack in front of Vira, and Vira is pleased.
[00:00-00:03] The glowing blank sheets glide together and settle one on top of another into a small, neat stack. The top sheet glows brightest.
[00:03-00:05] Vira's eyes change from upright bars into two thin white arches that curve upward, like closed smiling eyes. Vira gives one slow, soft pulse of light.
[00:05-00:08] The camera continues to pull back. The many dim grey sheets far behind recede and fade into darkness, leaving only Vira and the small stack.
Setting: an empty, endless dark space.
Lighting: low-key. Vira and the stack are the only bright things in the frame. The background falls to near-black.
Ambient noise: a deep, calm room tone.
SFX: soft paper sounds as the sheets settle, then one low, warm pulse.
{CHARACTER}
{STYLE}
```

### Shot 8. Morning hand-off

Mode: frames to video. Model: Veo 3.1 Quality. Ingredients for the start frame: `Vira neutral`.
The desk is empty of people on purpose. The person arrives after the film ends.

Start frame (image model):

```
Static wide shot, eye level, of a dark, tidy desk just before dawn. On the desk: a closed laptop with a plain blank lid, a plain ceramic cup with a thin curl of steam, and a small neat stack of softly glowing blank pale sheets. A window edge at the right of the frame shows the first thin band of warm dawn light. Vira, the orb from the attached reference image, hovers small above the desk at the left of centre, with neutral eyes, its glow reflected softly in the desk surface. The room is empty.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: wide shot, standard lens, eye level. The camera is locked off and does not move. Deep focus.
Subject and action: Vira waits calmly above a desk as morning arrives.
[00:00-00:04] The band of warm dawn light at the window edge slowly grows and spreads a little across the desk. Steam curls up from the cup.
[00:04-00:08] Vira bobs gently in place and blinks once. Its reflection glows softly on the desk beside the small stack of sheets.
Setting: a quiet, empty, tidy room with a dark desk, a closed laptop with a blank lid, a plain ceramic cup, and a small stack of blank sheets.
Lighting: low-key, moving toward soft warm morning light from the window at the right. Vira's orange glow is the second light source.
Ambient noise: a quiet room at dawn, with very faint birdsong through the glass.
SFX: none beyond the ambience.
{CHARACTER}
{STYLE}
```

### Shot 9. End card plate

The wordmark and the line under it are added in the edit, in the empty right two thirds.

Mode: frames to video with the same still as start frame and end frame. Model: Veo 3.1 Quality.
Ingredients for the start frame: `Vira neutral`.

Start frame (image model):

```
A near-black frame (#101010). Vira, the orb from the attached reference image, sits small in the lower left third with neutral eyes and a faint orange halo. The orb is about one eighth of the frame height. The right two thirds and the whole top half of the frame are completely empty, flat, dark space.
{CHARACTER}
{STYLE}
```

Clip:

```
Cinematography: wide shot, eye level. The camera is completely static and locked off. Sharp focus on the orb.
Subject and action: Vira rests in the lower left third of an empty dark frame, breathing gently.
[00:00-00:04] Vira swells very slightly and settles, bobbing a tiny amount.
[00:04-00:08] Vira blinks once, then returns to exactly its starting position, size, and brightness.
Setting: flat, empty, near-black space with nothing else in it.
Lighting: Vira is the only light. Its faint halo stays the same size.
Ambient noise: near silence, a very low room tone.
SFX: none.
{CHARACTER}
{STYLE}
```

## 7. C. Ambient background loop (video, 16:9, 8 seconds)

For the app or a landing page, behind real content. It has no Vira in it, so the live orb
component can sit on top without two orbs on screen.

Mode: frames to video with the same still as start frame and end frame. Model: Veo 3.1 Quality.
Using one image for both ends to get a loop is a practitioner technique. No Google page confirms
it gives a seamless loop, so check the seam and cross-fade the ends in the edit if it shows.

Start frame (image model, no ingredient):

```
Abstract, very dark background plate. Soft, blurred, warm orange light in the lower third of the frame, like embers seen through frosted glass, fading smoothly to flat near-black (#101010) across the upper two thirds. Very low contrast, no distinct shapes, no hard edges, no bright spots.
{STYLE}
```

Clip:

```
Cinematography: wide shot. The camera is completely static and locked off. Everything is soft and out of focus.
Subject and action: soft warm orange light drifts very slowly in the lower third of a dark frame.
[00:00-00:04] The blurred orange light drifts slowly to the right and dims a little.
[00:04-00:08] The light drifts slowly back to the left and returns to exactly its starting position and brightness.
Setting: abstract dark space with no objects.
Lighting: very low contrast. The upper two thirds stay flat near-black from start to finish.
Ambient noise: silence.
SFX: none.
{STYLE}
```

Strip the audio track before it goes on a page. Export a still of frame one as the poster image
and as the fallback for visitors who ask for reduced motion.

## 8. D. Stills for the web UI (image model)

The model will not hit `#101010` exactly. Before a still goes into the app, level its background
to `#101010` in an image editor, or fade its edges to transparent, so no rectangle shows against
the page. Where the UI already draws the live orb, prefer the live orb and use a still without
Vira behind it.

### D1. Empty state (1:1)

Shown when a list has nothing in it yet. Ingredient: `Vira curious`.

```
A flat near-black background (#101010) with nothing in it except one small scene at the centre. Vira, the orb from the attached reference image, floats above a single blank pale off-white paper sheet that lies flat on an unseen surface. Vira's eye on the left is short and a little lower, and its eye on the right is taller and a little higher, as if looking for more. The orb's warm glow lights the sheet softly from above and casts a small soft orange pool around it. The scene takes up the middle third of the frame. All four edges of the image are flat, even, near-black.
Front view, eye level, sharp focus, soft even shading.
{CHARACTER}
{STYLE}
```

### D2. Hero background (16:9)

Sits behind a headline and buttons on the left. Ingredient: `Vira neutral`.

```
Wide shot, eye level. Vira, the orb from the attached reference image, floats in the right third of the frame with neutral eyes, about one quarter of the frame height. Behind and around it, a sparse field of blank pale off-white paper sheets hangs in dark space, receding into the distance and going out of focus, the nearest few catching orange light on their edges. The left two thirds of the frame are calm, almost empty, flat near-black (#101010), with only a few very faint, very dim sheets.
Shallow depth of field, low-key lighting, Vira is the only light source.
{CHARACTER}
{STYLE}
```

### D3. Social share card plate (generate at 16:9, crop to 1200 by 630)

The title is set in Michroma in the edit, in the empty area. Keep Vira and the stack inside the
middle of the lower left quarter so nothing is lost in the crop. Ingredient: `Vira happy`.

```
Wide shot, eye level, on a flat near-black background (#101010). In the lower left quarter of the frame, Vira, the orb from the attached reference image, floats beside a small neat stack of softly glowing blank pale sheets. Vira's eyes are two thin white arches that curve upward, like closed smiling eyes. The orb is about one fifth of the frame height. The whole top half of the frame and the whole right half of the frame are completely empty, flat, even, near-black space, kept clear for a title.
Sharp focus, soft even shading, faint orange halo around the orb only.
{CHARACTER}
{STYLE}
```

## 9. Vertical variant (9:16)

Do not crop the 16:9 clips. Start a second project set to 9:16 and regenerate, because start
frames and clips should share one aspect ratio. The style block, the character block, and the
Vira reference stills stay the same. Remake each start frame at 9:16 with these changes:

- Put Vira on the vertical centre line, in the middle third of the height.
- Stack things top to bottom instead of left to right. In shot 3 the camera rises past panels
  stacked into the distance, or use two panels instead of four. In shot 5 the sheets fall from
  the top, kept sheets hover beside Vira, and the rest sink out of the bottom.
- Keep the top 15 percent and the bottom 25 percent of the frame free of anything important,
  since app buttons and captions cover those areas on most platforms.
- For the end card, place Vira in the lower third, centred, and keep the upper half empty for
  the wordmark.

Flow lists both 16:9 and 9:16 for text to video and frames to video on every model. Ingredients
at 9:16 were not tested for this file.

## 10. Voiceover draft

About 95 words, which fits 60 seconds at a calm pace with room to breathe. Record it separately
and lay it in during the edit. Do not ask the model to speak it. Check every claim against the
product before recording.

> Every day, government portals publish thousands of tender notices. Somewhere in them are the
> few your firm should bid on. This is Vira. Overnight, Vira reads the sources that are open to
> read. Where a source is closed, Vira leaves it closed. Most notices are not for you, so Vira
> sets them aside. For each one it keeps, it points to the line in the notice that says why. By
> morning there is a short list, with the reasons attached. You decide what to bid on. Chanakya,
> by Wayam AI.

Rough timing against the cut: sentences one and two over shot 1, "This is Vira" on the eye-open
in shot 2, the two source sentences over shots 3 and 4, "sets them aside" over shot 5, the
evidence sentence over shots 6 and R, "short list" over shot 7, "You decide" over shot 8, and the
sign-off over shot 9.

## 11. Troubleshooting

| Problem | Likely cause | What to do |
| --- | --- | --- |
| Letters, numbers, or fake print on the sheets | Paper suggests print to the model | Keep "blank, smooth, unmarked" next to every mention of a sheet. Push the sheets further from the lens or out of focus. Fix the start frame first. |
| Captions or subtitles burned into the clip | A known Veo habit, reported widely by users | Make sure the prompt has no quotation marks and no speech. Keep "captions, subtitles" in the `Avoid:` list. Regenerate. If one keeps appearing in the same place, crop or remove it. |
| Vira gains a mouth, pupils, limbs, or a glassy look | Reference not attached, or the orb is too small in frame | Follow the drift steps in section 3. Regenerate the start frame with the Vira still attached. |
| Eyes change shape mid-clip | Too much action, or the expression change is asked for too late | Use an end frame with the target mood still. Or generate with one mood and cut on the change. |
| The clip cuts to a new angle between timestamps | Timestamps read as separate shots | Remove the timestamps and write the beats as one sentence joined by "then". Keep "One continuous shot with no cuts." |
| Camera wanders or does two moves | Two camera instructions, or none | Name one move only. For static shots say "locked off and does not move". Flow's camera edit works best on clips that have no camera motion yet. |
| Colours drift toward red, yellow, or neon | Colour words too loose, or no reference | Re-attach the Vira still. Check a frame against the app. Small shifts can be corrected in the grade. |
| A second saturated colour appears, such as blue screens | Props suggest it, especially laptops and windows | Keep props closed, blank, and unlit. Restate that orange is the only saturated colour. |
| The shutter in shot 4 opens or glows | The model wants to resolve the scene | Add the end frame with the shutter still closed. Reject every take where it changes. |
| Music or a voice in the audio | Model default | Keep "speech, singing, music" in the `Avoid:` list and keep the two sound lines specific. The final mix replaces clip audio anyway, so mute the clip if needed. |
| The loop has a visible jump | Start and end do not match exactly | Use the same still for both frames. Cross-fade the last half second over the first in the edit. |
| A flag, seal, or official-looking building appears | Words such as "government" or "portal" in the prompt | Never use those words in a prompt. The prompts here say "panels" and "sheets" for that reason. Discard the take. |
| Flow says the feature is not supported | The chosen model does not offer that mode | Check the model list in section 2. Ingredients need Veo 3.1 Fast, Veo 3.1 Lite, or Omni Flash. Extend needs Veo 3.1 Lite. |

## 12. Not confirmed

- Whether Flow has a separate negative prompt field. The Flow help pages checked do not mention
  one, so the `Avoid:` list goes in the prompt text.
- Whether Veo honours hex colour codes. No Google source says so.
- Whether using one image as both start and end frame gives a seamless loop. It is a common
  practitioner method, not a documented one.
- How many seconds one Extend adds in Flow. The Gemini API documents 7 seconds per extension at
  720p. The Flow help pages do not give a figure.
- Export resolution and upscaling options for Veo clips in Flow. The API offers 1080p and 4k at
  8 seconds. Check the download menu in the app.
- Which aspect ratios the Flow image model offers. The help page says the ratio is chosen in the
  image settings but does not list the options. If 1:1 is missing, generate at 16:9 and crop.
- Credit costs. They change, and Flow shows the current cost in the prompt box settings.
- The edit tools on Omni Flash (insert, remove, video to video) are limited to certain countries,
  and their availability in India was not checked.

## 13. Sources

Google:

- Veo 3.1 prompting guide (formula, audio syntax, timestamps, frames and ingredients workflows):
  https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1
- Flow models and supported features (which model supports which mode and length):
  https://support.google.com/flow/answer/16352836?hl=en
- Create videos in Flow (frames, ingredients, plain backgrounds, keep prompt and references
  consistent): https://support.google.com/flow/answer/16894016?hl=en
- Edit videos and build scenes in Flow (Extend, Save frame, Scenebuilder):
  https://support.google.com/flow/answer/16935718?hl=en
- Create and edit images in Flow: https://support.google.com/flow/answer/16729550?hl=en
- Veo in the Gemini API (4, 6, 8 seconds, 16:9 and 9:16, up to three reference images, extension):
  https://ai.google.dev/gemini-api/docs/veo
- Vertex video prompt guide (camera, lens, and lighting terms, and the advice on "no" and "don't"):
  https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide
- 5 tips for getting started with Flow (up to three ingredients, repeat key details across
  prompts): https://blog.google/technology/ai/flow-video-tips/
- 4 ways to refine your content in Flow (image edits without a full regenerate, camera edit
  works best on clips without camera motion):
  https://blog.google/technology/google-labs/flow-refine-videos/

Practitioner reports, used only for failure modes and marked as such above:

- Burned-in captions and unreliable text in Veo clips:
  https://www.veo3ai.io/blog/veo-3-remove-subtitles-captions-fix-2026

The advice to keep one action and one camera move per clip came up in third-party guides found
by search. Those pages were not read in full, so treat it as a working rule, not a sourced fact.
