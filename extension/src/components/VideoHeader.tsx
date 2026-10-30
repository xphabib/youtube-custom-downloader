import { thumbnailUrl } from "../lib/youtube";
import type { VideoInfo } from "../lib/types";

export function VideoHeader({ video, label }: { video: VideoInfo; label?: string }) {
  return (
    <div className="video-header">
      <img className="thumb" src={thumbnailUrl(video.videoId)} alt="" width={96} height={54} />
      <div className="video-text">
        {label && <span className="eyebrow">{label}</span>}
        <p className="video-title" title={video.title ?? undefined}>{video.title ?? "Untitled video"}</p>
        <p className="muted small">{video.isShort ? "Short · " : ""}{video.videoId}</p>
      </div>
    </div>
  );
}

export function NotAVideo() {
  return (
    <div className="card notice-card">
      <p className="strong">Open a YouTube video to add clips</p>
      <p className="muted small">
        ytclip works on youtube.com/watch, /shorts and youtu.be pages. You can still manage the queue below.
      </p>
    </div>
  );
}
