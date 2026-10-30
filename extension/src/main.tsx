import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { chromeTransport, NativeClient } from "./lib/native";
import { chromeStore, ClipStorage, KEYS, type PageMark } from "./lib/storage";
import { getActiveVideo, readPlayerState, watchActiveTab } from "./lib/tab";
import "./styles.css";

function watchMarks(onMark: (mark: PageMark) => void) {
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
    const mark = changes[KEYS.mark]?.newValue as PageMark | undefined;
    if (area === "local" && mark) onMark(mark);
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

const deps = {
  client: new NativeClient(chromeTransport()),
  storage: new ClipStorage(chromeStore()),
  getActiveVideo,
  readPlayerState,
  watchActiveTab,
  watchMarks,
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App {...deps} />
  </StrictMode>,
);
