// Content script: adds a ytclip toolbar to the YouTube watch page's action row, left of
// Like/Share: Start/End buttons that take the player's current time, dropdowns for aspect
// ratio, crop mode and resolution, and Add to queue. Everything goes through the saved
// draft, so the side panel shows the same values live or the next time it opens.

import { aspectValue, defaultRect, fitAspect } from "../lib/crop";
import { ADD_CLIP, type AddClipMessage, type AddClipResult } from "../lib/messages";
import { MODE_OPTIONS, RATIO_OPTIONS, RESOLUTION_OPTIONS, type FormatOption } from "../lib/options";
import { chromeStore, ClipStorage } from "../lib/storage";
import { secondsToTimestamp } from "../lib/time";
import type { ClipDraft, CropRect } from "../lib/types";
import { isValid, toRequest, validateDraft } from "../lib/validation";
import { cleanTitle, parseVideoUrl } from "../lib/youtube";
import { CROP_CSS, openCropEditor, type CropEditor } from "./cropEditor";

const ROOT_ID = "ytclip-marks";
const STYLE_ID = "ytclip-marks-style";
const MENU_ID = "ytclip-menu";
const TOAST_ID = "ytclip-toast";
const SVG_NS = "http://www.w3.org/2000/svg";

const ICONS = {
  start: "M5 5h2v14H5V5Zm4 0 10 7-10 7V5Z", // bar + arrow: clip starts here
  end: "M5 5l10 7-10 7V5Zm12 0h2v14h-2V5Z", // arrow + bar: clip ends here
  add: "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6V5Z",
  chevron: "M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41Z",
  check: "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17Z",
  area: "M3 3h6v2H5v4H3V3Zm12 0h6v6h-2V5h-4V3ZM3 15h2v4h4v2H3v-6Zm16 0h2v6h-6v-2h4v-4Z", // crop corners
};

const TIME_FIELDS = [
  { field: "start", label: "Start", title: "ytclip: set the clip start to the current time" },
  { field: "end", label: "End", title: "ytclip: set the clip end to the current time" },
] as const;

type FormatField = "aspectRatio" | "cropMode" | "resolution";
const FORMAT_FIELDS: { field: FormatField; title: string; options: readonly FormatOption<string>[] }[] = [
  { field: "aspectRatio", title: "ytclip: aspect ratio", options: RATIO_OPTIONS },
  { field: "cropMode", title: "ytclip: crop mode", options: MODE_OPTIONS },
  { field: "resolution", title: "ytclip: resolution", options: RESOLUTION_OPTIONS },
];

// YouTube's own button classes (copied from the Like/Share buttons), so ours match them
// in both themes, including hover and pressed states.
const BASE_CLASSES = [
  "ytSpecButtonShapeNextHost", "ytSpecButtonShapeNextMono", "ytSpecButtonShapeNextSizeM",
  "ytSpecButtonShapeNextEnableBackdropFilterExperiment", "ytSpecButtonShapeNextMainstageIconSize",
  "ytSpecButtonShapeNextMainstagePadding",
];
const TONAL = [...BASE_CLASSES, "ytSpecButtonShapeNextTonal"];
const FILLED = [...BASE_CLASSES, "ytSpecButtonShapeNextFilled"]; // like Subscribe

const storage = new ClipStorage(chromeStore());

const CSS = `
ytd-menu-renderer:has(> #${ROOT_ID}) { align-items: center; }
#${ROOT_ID} {
  display: flex; flex: none; align-self: center; align-items: center; gap: 8px;
  height: 36px; margin: 0 8px 0 0; padding: 0;
}
/* YouTube's buttons sit in a button-view-model wrapper that sizes them; ours have none. */
#${ROOT_ID} button {
  display: inline-flex; align-items: center; flex: none; box-sizing: border-box;
  height: 36px; margin: 0; white-space: nowrap; vertical-align: middle;
}
#${ROOT_ID} button:disabled { opacity: 0.5; cursor: default; }
#${ROOT_ID} .ytclip-text { display: flex; align-items: center; line-height: 36px; }
#${ROOT_ID} .ytclip-time { margin-left: 4px; font-variant-numeric: tabular-nums; opacity: 0.75; }
#${ROOT_ID} .ytclip-chevron { width: 20px; height: 20px; margin: 0 -6px 0 2px; }
#${ROOT_ID} button.ytclip-flash { background: #3ea6ff; color: #0f0f0f; }

#${MENU_ID} {
  position: fixed; z-index: 2300; min-width: 180px; padding: 8px 0; border-radius: 12px;
  background: var(--yt-spec-menu-background, #fff); color: var(--yt-spec-text-primary, #0f0f0f);
  box-shadow: 0 4px 32px 0 rgba(0, 0, 0, 0.1), 0 0 0 1px var(--yt-spec-10-percent-layer, rgba(0, 0, 0, 0.1));
  font: 400 14px/20px "Roboto", "Arial", sans-serif;
}
#${MENU_ID} button {
  display: flex; align-items: center; gap: 12px; width: 100%; padding: 8px 16px 8px 12px;
  border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer;
}
#${MENU_ID} button:hover, #${MENU_ID} button:focus-visible {
  background: var(--yt-spec-10-percent-layer, rgba(0, 0, 0, 0.1)); outline: none;
}
#${MENU_ID} svg { width: 20px; height: 20px; flex: none; }
#${MENU_ID} button:not([aria-checked="true"]) svg { visibility: hidden; }
#${MENU_ID} .ytclip-hint { display: block; font-size: 12px; color: var(--yt-spec-text-secondary, #606060); }

#${TOAST_ID} {
  position: fixed; z-index: 2400; left: 24px; bottom: 24px; max-width: 420px; padding: 14px 16px;
  border-radius: 8px; background: #212121; color: #fff; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  font: 400 14px/20px "Roboto", "Arial", sans-serif; transition: opacity 0.2s ease;
}
`;

// ---- page helpers ----------------------------------------------------------------------

/** `00:01:23` -> `1:23`, `01:02:03` -> `1:02:03` for the compact button label. */
function shortTime(value: string): string {
  return value.replace(/^00:/, "").replace(/^0(\d)/, "$1");
}

function currentVideoId(): string | null {
  return parseVideoUrl(location.href)?.videoId ?? null;
}

function player(): HTMLVideoElement | null {
  return document.querySelector<HTMLVideoElement>("#movie_player video") ?? document.querySelector("video");
}

function pageTitle(): string | null {
  const heading = document.querySelector("ytd-watch-metadata h1, h1.title");
  return heading?.textContent?.trim() || cleanTitle(document.title);
}

function svgIcon(path: string, className?: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "currentColor");
  svg.setAttribute("aria-hidden", "true");
  svg.style.cssText = "pointer-events: none; display: block;";
  if (className) svg.setAttribute("class", className);
  const shape = document.createElementNS(SVG_NS, "path");
  shape.setAttribute("d", path);
  svg.append(shape);
  return svg;
}

let toastTimer: ReturnType<typeof setTimeout> | undefined;
function toast(text: string) {
  let el = document.getElementById(TOAST_ID);
  if (!el) {
    el = document.createElement("div");
    el.id = TOAST_ID;
    el.setAttribute("role", "status");
    document.body.append(el);
  }
  el.textContent = `ytclip: ${text}`;
  el.style.opacity = "1";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el?.remove(), 4000);
}

function flash(button: HTMLButtonElement) {
  button.classList.add("ytclip-flash");
  setTimeout(() => button.classList.remove("ytclip-flash"), 300);
}

// ---- toolbar -----------------------------------------------------------------------------

function makeButton(classes: string[], title: string, iconPath: string | null, label: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = [...classes, ...(iconPath ? ["ytSpecButtonShapeNextIconLeading"] : [])].join(" ");
  button.title = title;
  button.setAttribute("aria-label", title);
  if (iconPath) {
    const wrap = document.createElement("div");
    wrap.className = "ytSpecButtonShapeNextIcon ytSpecButtonShapeNextElevatedContent";
    wrap.setAttribute("aria-hidden", "true");
    const svg = svgIcon(iconPath);
    svg.style.cssText += "width: 24px; height: 24px;";
    wrap.append(svg);
    button.append(wrap);
  }
  const text = document.createElement("div");
  text.className = "ytSpecButtonShapeNextButtonTextContent ytSpecButtonShapeNextElevatedContent ytclip-text";
  text.textContent = label;
  button.append(text);
  button.addEventListener("click", (event) => event.stopPropagation());
  return button;
}

function setText(button: HTMLButtonElement, label: string, extra?: { time?: string; chevron?: boolean }) {
  const text = button.querySelector(".ytclip-text");
  if (!text) return;
  text.textContent = label;
  if (extra?.time) {
    const time = document.createElement("span");
    time.className = "ytclip-time";
    time.textContent = shortTime(extra.time);
    text.append(time);
  }
  if (extra?.chevron) text.append(svgIcon(ICONS.chevron, "ytclip-chevron"));
}

/** Show the saved draft's values on the buttons. */
async function render(root: HTMLElement, videoId: string) {
  const { draft } = await storage.draftFor(videoId);
  for (const { field, label } of TIME_FIELDS) {
    const button = root.querySelector<HTMLButtonElement>(`button[data-field="${field}"]`);
    if (button) setText(button, label, { time: draft[field] || undefined });
  }
  for (const { field, options } of FORMAT_FIELDS) {
    const button = root.querySelector<HTMLButtonElement>(`button[data-field="${field}"]`);
    const label = options.find((o) => o.value === draft[field])?.label ?? draft[field];
    if (button) setText(button, label, { chevron: true });
  }
  await syncCrop(root, videoId, draft);
}

// ---- custom crop ---------------------------------------------------------------------------

let editor: CropEditor | null = null;

function frameAspect(): number {
  const video = player();
  return video?.videoWidth && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9;
}

function closeEditor() {
  editor?.close();
  editor = null;
  const root = document.getElementById(ROOT_ID);
  if (root?.dataset.videoId) void render(root, root.dataset.videoId);
}

async function openEditor(videoId: string) {
  if (editor) return;
  const { draft } = await storage.draftFor(videoId);
  // Checked again after the await: two renders can race to open the editor.
  const playerEl = document.getElementById("movie_player");
  const video = player();
  if (editor || !playerEl || !video) return;
  const target = aspectValue(draft.aspectRatio);
  const rect = draft.cropRect ?? defaultRect(frameAspect(), target);
  editor = openCropEditor({
    player: playerEl, video, rect, target,
    label: `${draft.aspectRatio} area · drag to move, corners to resize`,
    onCommit: (next: CropRect) => void storage.patchDraft(videoId, { cropRect: next }),
    onDone: closeEditor,
  });
  if (!draft.cropRect) await storage.patchDraft(videoId, { cropRect: rect });
  else void render(document.getElementById(ROOT_ID)!, videoId);
}

/** Keep the Area button, the editor and the saved rect in step with the draft. */
async function syncCrop(root: HTMLElement, videoId: string, draft: ClipDraft) {
  const area = root.querySelector<HTMLButtonElement>('button[data-field="cropRect"]');
  const custom = draft.cropMode === "custom";
  if (area) {
    area.hidden = !custom;
    area.style.display = custom ? "" : "none";
    setText(area, editor ? "Done" : "Area");
    area.setAttribute("aria-pressed", String(Boolean(editor)));
  }
  if (!custom) {
    if (editor) closeEditor();
    return;
  }
  if (!draft.cropRect) {
    // Just switched to custom for this video (here or in the side panel). A fresh video
    // that only inherits "custom" from the preferences waits for the Area button.
    if ((await storage.draft())?.videoId === videoId) await openEditor(videoId);
    return;
  }
  // A new aspect ratio reshapes the box around its centre.
  const target = aspectValue(draft.aspectRatio);
  const r = draft.cropRect;
  const current = (r.width * frameAspect()) / r.height;
  if (Math.abs(current - target) / target > 0.01) {
    await storage.patchDraft(videoId, { cropRect: fitAspect(r, frameAspect(), target) });
    return;
  }
  editor?.update(r, target);
}

async function markTime(button: HTMLButtonElement, field: "start" | "end") {
  const videoId = currentVideoId();
  const video = player();
  if (!videoId || !video) return;
  await storage.patchDraft(videoId, { [field]: secondsToTimestamp(video.currentTime) });
  flash(button);
}

// ---- dropdown menu ---------------------------------------------------------------------

function closeMenu() {
  document.getElementById(MENU_ID)?.remove();
  document.querySelector(`#${ROOT_ID} button[aria-expanded="true"]`)?.setAttribute("aria-expanded", "false");
}

async function openMenu(button: HTMLButtonElement, field: FormatField, options: readonly FormatOption<string>[]) {
  const wasOpen = button.getAttribute("aria-expanded") === "true";
  closeMenu();
  const videoId = currentVideoId();
  if (wasOpen || !videoId) return;
  const { draft } = await storage.draftFor(videoId);

  const menu = document.createElement("div");
  menu.id = MENU_ID;
  menu.setAttribute("role", "menu");
  for (const option of options) {
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitemradio");
    item.setAttribute("aria-checked", String(option.value === draft[field]));
    const text = document.createElement("span");
    text.textContent = option.label;
    if (option.hint) {
      const hint = document.createElement("span");
      hint.className = "ytclip-hint";
      hint.textContent = option.hint;
      text.append(hint);
    }
    item.append(svgIcon(ICONS.check), text);
    item.addEventListener("click", (event) => {
      event.stopPropagation();
      closeMenu();
      void storage.patchDraft(videoId, { [field]: option.value } as Partial<ClipDraft>);
    });
    menu.append(item);
  }
  document.body.append(menu);
  const rect = button.getBoundingClientRect();
  // Open upwards, over the video area; fall back to below when there is no room above.
  const above = rect.top - menu.offsetHeight - 4;
  menu.style.top = `${above >= 8 ? above : rect.bottom + 4}px`;
  menu.style.left = `${Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8)}px`;
  button.setAttribute("aria-expanded", "true");
  menu.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
}

document.addEventListener("click", (event) => {
  if (!(event.target as Element | null)?.closest?.(`#${MENU_ID}`)) closeMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenu();
});
window.addEventListener("scroll", closeMenu, { passive: true });

// ---- add to queue --------------------------------------------------------------------------

function sendClip(message: AddClipMessage): Promise<AddClipResult> {
  return chrome.runtime.sendMessage<AddClipMessage, AddClipResult>(message);
}

async function addToQueue(button: HTMLButtonElement) {
  const videoId = currentVideoId();
  const video = parseVideoUrl(location.href);
  if (!videoId || !video) return;
  const { draft } = await storage.draftFor(videoId);
  const duration = player()?.duration;
  const validation = validateDraft(draft, Number.isFinite(duration) ? duration : null);
  if (!isValid(validation)) {
    const errors = validation.errors;
    toast(errors.start ?? errors.end ?? Object.values(errors)[0] ?? "Check the clip settings in the side panel.");
    return;
  }
  const clip = toRequest(draft, { ...video, title: pageTitle() });
  button.disabled = true;
  try {
    let result = await sendClip({ type: ADD_CLIP, clip, allowDuplicate: false });
    if (result.status === "duplicate") {
      if (!confirm(`ytclip: an identical clip is already queued as job #${result.existingJob.id}. Add it anyway?`)) return;
      result = await sendClip({ type: ADD_CLIP, clip, allowDuplicate: true });
    }
    if (result.status === "error" || result.status === "duplicate") {
      toast(result.status === "error" ? result.message : "This clip is already queued.");
      return;
    }
    // Same as the side panel: the next clip starts where this one ended.
    await storage.patchDraft(videoId, { start: draft.end, end: "", outputName: "" });
    flash(button);
    toast(
      result.status === "queued"
        ? `Queued as job #${result.job.id}. Press Download in the side panel to process it.`
        : "Saved in the browser. It will be queued when ytclip is connected.",
    );
  } catch (error) {
    toast(error instanceof Error ? error.message : String(error));
  } finally {
    button.disabled = false;
  }
}

// ---- mounting ------------------------------------------------------------------------------

function build(videoId: string): HTMLElement {
  const root = document.createElement("div");
  root.id = ROOT_ID;
  root.dataset.videoId = videoId;
  for (const { field, label, title } of TIME_FIELDS) {
    const button = makeButton(TONAL, title, ICONS[field], label);
    button.dataset.field = field;
    button.addEventListener("click", () => void markTime(button, field));
    root.append(button);
  }
  for (const { field, title, options } of FORMAT_FIELDS) {
    const button = makeButton(TONAL, title, null, "");
    button.dataset.field = field;
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    button.addEventListener("click", () => void openMenu(button, field, options));
    root.append(button);
  }
  const area = makeButton(TONAL, "ytclip: choose the custom crop area on the video", ICONS.area, "Area");
  area.dataset.field = "cropRect";
  area.style.display = "none";
  area.addEventListener("click", () => (editor ? closeEditor() : void openEditor(videoId)));
  root.append(area);
  const add = makeButton(FILLED, "ytclip: add this clip to the queue", ICONS.add, "Add");
  add.addEventListener("click", () => void addToQueue(add));
  root.append(add);
  return root;
}

/** Insert, refresh or remove the toolbar to match the current page. Cheap when nothing changed. */
function sync() {
  const existing = document.getElementById(ROOT_ID);
  // After the extension is reloaded this script is orphaned and storage calls would throw.
  if (!chrome.runtime?.id) {
    existing?.remove();
    closeMenu();
    if (editor) closeEditor();
    observer.disconnect();
    return;
  }
  const videoId = currentVideoId();
  // Like/Dislike and Share live in #top-level-buttons-computed, which YouTube renders itself
  // and hides foreign children in. Its parent ytd-menu-renderer is the same flex row, so we
  // sit right before it (left of Like) as a sibling.
  const anchor = document.querySelector("ytd-watch-metadata #top-row #actions ytd-menu-renderer > #top-level-buttons-computed");
  if (!videoId || !anchor) {
    existing?.remove();
    closeMenu();
    if (editor) closeEditor();
    return;
  }
  if (existing && existing.nextElementSibling === anchor && existing.dataset.videoId === videoId) return;
  existing?.remove();
  closeMenu();
  if (editor) closeEditor();
  const root = build(videoId);
  anchor.before(root);
  void render(root, videoId);
}

if (!document.getElementById(STYLE_ID)) {
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS + CROP_CSS;
  document.head.append(style);
}

// YouTube is a single-page app that re-renders the action row on navigation.
let scheduled = false;
const observer = new MutationObserver(() => {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    sync();
  });
});
observer.observe(document.documentElement, { childList: true, subtree: true });
document.addEventListener("yt-navigate-finish", sync);

// Keep the buttons in step with the draft, whether the page or the side panel changed it.
chrome.storage.onChanged.addListener((changes, area) => {
  const root = document.getElementById(ROOT_ID);
  if (area === "local" && ("draft" in changes || "preferences" in changes) && root?.dataset.videoId) {
    void render(root, root.dataset.videoId);
  }
});

sync();
