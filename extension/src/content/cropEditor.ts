// The custom crop box drawn over the YouTube player: drag inside it to move, drag a corner
// to resize (aspect locked), or press anywhere else to move it there. The rect is saved
// when a drag ends; Done or Escape closes the editor.

import { contentBox, moveRect, resizeRect, type Corner } from "../lib/crop";
import type { CropRect } from "../lib/types";

export const OVERLAY_ID = "ytclip-crop";

export const CROP_CSS = `
#${OVERLAY_ID} {
  position: absolute; z-index: 1000; overflow: hidden; cursor: crosshair; touch-action: none;
  user-select: none; font: 500 13px/18px "Roboto", "Arial", sans-serif;
}
#${OVERLAY_ID} .ytclip-crop-box {
  position: absolute; box-sizing: border-box; cursor: move;
  outline: 2px solid #fff; box-shadow: 0 0 0 100vmax rgba(0, 0, 0, 0.55);
  background:
    linear-gradient(to right, transparent calc(33.33% - 0.5px), rgba(255,255,255,0.35) 0,
      rgba(255,255,255,0.35) calc(33.33% + 0.5px), transparent 0,
      transparent calc(66.66% - 0.5px), rgba(255,255,255,0.35) 0,
      rgba(255,255,255,0.35) calc(66.66% + 0.5px), transparent 0),
    linear-gradient(to bottom, transparent calc(33.33% - 0.5px), rgba(255,255,255,0.35) 0,
      rgba(255,255,255,0.35) calc(33.33% + 0.5px), transparent 0,
      transparent calc(66.66% - 0.5px), rgba(255,255,255,0.35) 0,
      rgba(255,255,255,0.35) calc(66.66% + 0.5px), transparent 0);
}
#${OVERLAY_ID} .ytclip-crop-handle {
  position: absolute; width: 16px; height: 16px; margin: -8px 0 0 -8px; border-radius: 50%;
  background: #fff; box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.4), 0 1px 4px rgba(0, 0, 0, 0.5);
}
#${OVERLAY_ID} .ytclip-crop-handle[data-corner="nw"] { left: 0; top: 0; cursor: nwse-resize; }
#${OVERLAY_ID} .ytclip-crop-handle[data-corner="ne"] { left: 100%; top: 0; cursor: nesw-resize; }
#${OVERLAY_ID} .ytclip-crop-handle[data-corner="sw"] { left: 0; top: 100%; cursor: nesw-resize; }
#${OVERLAY_ID} .ytclip-crop-handle[data-corner="se"] { left: 100%; top: 100%; cursor: nwse-resize; }
#${OVERLAY_ID} .ytclip-crop-bar {
  position: absolute; top: 12px; left: 50%; transform: translateX(-50%); display: flex;
  align-items: center; gap: 12px; padding: 6px 6px 6px 14px; border-radius: 18px;
  background: rgba(15, 15, 15, 0.85); color: #fff; white-space: nowrap; cursor: default;
}
#${OVERLAY_ID} .ytclip-crop-bar button {
  height: 28px; padding: 0 14px; border: 0; border-radius: 14px; cursor: pointer;
  background: #fff; color: #0f0f0f; font: inherit;
}
`;

export interface CropEditor {
  /** Show a rect saved elsewhere (ignored while the user is dragging). */
  update(rect: CropRect, target: number): void;
  close(): void;
}

interface Options {
  player: HTMLElement;
  video: HTMLVideoElement;
  rect: CropRect;
  target: number;
  label: string;
  onCommit: (rect: CropRect) => void;
  onDone: () => void;
}

type Drag =
  | { kind: "move"; startX: number; startY: number; start: CropRect }
  | { kind: "resize"; corner: Corner; start: CropRect };

export function openCropEditor({ player, video, rect, target, label, onCommit, onDone }: Options): CropEditor {
  let current = rect;
  let aspect = target;
  let drag: Drag | null = null;

  const overlay = document.createElement("div");
  overlay.id = OVERLAY_ID;
  const box = document.createElement("div");
  box.className = "ytclip-crop-box";
  for (const corner of ["nw", "ne", "sw", "se"] as const) {
    const handle = document.createElement("div");
    handle.className = "ytclip-crop-handle";
    handle.dataset.corner = corner;
    box.append(handle);
  }
  const bar = document.createElement("div");
  bar.className = "ytclip-crop-bar";
  const hint = document.createElement("span");
  hint.textContent = label;
  const done = document.createElement("button");
  done.type = "button";
  done.textContent = "Done";
  done.addEventListener("click", () => onDone());
  bar.append(hint, done);
  overlay.append(box, bar);
  player.append(overlay);

  const frameAspect = () => (video.videoWidth && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9);

  /** Keep the overlay exactly over the picture, wherever the player letterboxes it. */
  function layout() {
    const p = player.getBoundingClientRect();
    const v = video.getBoundingClientRect();
    const picture = contentBox(v.width, v.height, video.videoWidth, video.videoHeight);
    overlay.style.left = `${v.left - p.left + picture.left}px`;
    overlay.style.top = `${v.top - p.top + picture.top}px`;
    overlay.style.width = `${picture.width}px`;
    overlay.style.height = `${picture.height}px`;
  }

  function paint() {
    box.style.left = `${current.x * 100}%`;
    box.style.top = `${current.y * 100}%`;
    box.style.width = `${current.width * 100}%`;
    box.style.height = `${current.height * 100}%`;
  }

  function pointer(event: PointerEvent): [number, number] {
    const r = overlay.getBoundingClientRect();
    return [(event.clientX - r.left) / r.width, (event.clientY - r.top) / r.height];
  }

  overlay.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || bar.contains(event.target as Node)) return;
    event.preventDefault();
    const [px, py] = pointer(event);
    const corner = (event.target as HTMLElement).dataset.corner as Corner | undefined;
    if (corner) {
      drag = { kind: "resize", corner, start: current };
    } else {
      if (!box.contains(event.target as Node)) {
        // Pressing outside the box brings it there, then the drag continues as a move.
        current = moveRect(current, px - (current.x + current.width / 2), py - (current.y + current.height / 2));
        paint();
      }
      drag = { kind: "move", startX: px, startY: py, start: current };
    }
    overlay.setPointerCapture(event.pointerId);
  });

  overlay.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const [px, py] = pointer(event);
    current =
      drag.kind === "move"
        ? moveRect(drag.start, px - drag.startX, py - drag.startY)
        : resizeRect(drag.start, drag.corner, px, py, frameAspect(), aspect);
    paint();
  });

  const finish = (event: PointerEvent) => {
    if (!drag) return;
    drag = null;
    if (overlay.hasPointerCapture(event.pointerId)) overlay.releasePointerCapture(event.pointerId);
    onCommit(current);
  };
  overlay.addEventListener("pointerup", finish);
  overlay.addEventListener("pointercancel", finish);

  // Keep YouTube from treating our drags as clicks on the player (play/pause, fullscreen).
  for (const type of ["mousedown", "mouseup", "click", "dblclick", "touchstart"]) {
    overlay.addEventListener(type, (event) => event.stopPropagation());
  }

  const onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") onDone();
  };
  document.addEventListener("keydown", onKey);
  const resize = new ResizeObserver(layout);
  resize.observe(player);
  resize.observe(video);
  video.addEventListener("loadedmetadata", layout);
  layout();
  paint();

  return {
    update(rect, target) {
      aspect = target;
      if (drag) return;
      current = rect;
      paint();
    },
    close() {
      resize.disconnect();
      video.removeEventListener("loadedmetadata", layout);
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    },
  };
}
